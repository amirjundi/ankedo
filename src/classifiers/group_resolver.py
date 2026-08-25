"""Resolve free text to a canonical TargetGroup.

Group identity is the hinge the whole classifier turns on: a trope fires only when
its target group is matched in the context bundle (FR-CL-8). If "الإيزيديين" in a post
fails to resolve to the `yazidi` group, the trope silently never fires and the hate
speech is missed with no error anywhere.

Matching is done on normalized text so script and orthography variants collapse
together, and longest-alias-first so "faili kurd" never loses to "kurd".
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.normalizer import Normalizer
from src.models.target_group import TargetGroup

log = structlog.get_logger()


class GroupResolver:
    """Maps text to canonical target groups via slug, display names and aliases."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.normalizer = Normalizer()
        # {normalized alias: slug}, built once per resolver instance
        self._index: dict[str, str] | None = None
        self._by_slug: dict[str, TargetGroup] = {}

    async def _build_index(self) -> dict[str, str]:
        if self._index is not None:
            return self._index

        stmt = select(TargetGroup).where(TargetGroup.enabled.is_(True))
        groups = (await self.session.execute(stmt)).scalars().all()

        index: dict[str, str] = {}
        for group in groups:
            self._by_slug[group.slug] = group
            surfaces = [
                group.slug,
                group.display_name_en,
                group.display_name_ar,
                group.display_name_ku,
                *(group.aliases or []),
            ]
            for surface in surfaces:
                if not surface:
                    continue
                key = self.normalizer.normalize(str(surface))
                if key:
                    index[key] = group.slug

        self._index = index
        log.debug("Group alias index built", groups=len(groups), aliases=len(index))
        return index

    async def resolve_all(self, text: str) -> list[str]:
        """Return slugs of every group mentioned in `text`, most specific first.

        Longest alias wins: a post mentioning "الكرد الفيليون" resolves to
        `faili-kurd` and not merely to a broader group sharing a substring.
        """
        if not text:
            return []

        index = await self._build_index()
        haystack = self.normalizer.normalize(text)

        found: list[str] = []
        for alias in sorted(index, key=len, reverse=True):
            if alias and alias in haystack:
                slug = index[alias]
                if slug not in found:
                    found.append(slug)
        return found

    async def resolve(self, text: str) -> str | None:
        """Return the single most specific group mentioned, or None."""
        matches = await self.resolve_all(text)
        return matches[0] if matches else None

    async def get(self, slug: str) -> TargetGroup | None:
        await self._build_index()
        return self._by_slug.get(slug)

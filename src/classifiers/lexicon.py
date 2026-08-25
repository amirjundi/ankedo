"""
Fast first-pass lexicon lookup.

Two things the previous version got wrong, both of which matter:

* it loaded the entire table on every post — the scan is now compiled once and cached,
  invalidated by a cheap fingerprint of the table
* it matched bare substrings, so a short term matched inside unrelated words. Terms are
  now matched on token boundaries.

FR-CL-4 lives here: `is_explicit` entries flag regardless of the parent post's topic,
while non-explicit entries only contribute when a target group is present in context.
"""
from __future__ import annotations

import re

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.normalizer import Normalizer
from src.models.lexicon_entry import LexiconEntry, TermScope

log = structlog.get_logger()


class LexiconMatcher:
    """Matches normalized text against the lexicon."""

    # Cache is process-wide: the lexicon changes on pack install, not per request.
    _cache: dict | None = None
    _fingerprint: tuple | None = None

    def __init__(self, session: AsyncSession):
        self.session = session
        self.normalizer = Normalizer()

    async def _current_fingerprint(self) -> tuple:
        """Cheap change-detector: row count plus latest update timestamp."""
        row = (
            await self.session.execute(
                select(func.count(LexiconEntry.id), func.max(LexiconEntry.updated_at))
            )
        ).one()
        return (row[0], str(row[1]))

    async def _index(self) -> dict:
        fingerprint = await self._current_fingerprint()
        if LexiconMatcher._cache is not None and LexiconMatcher._fingerprint == fingerprint:
            return LexiconMatcher._cache

        stmt = select(LexiconEntry).where(LexiconEntry.enabled.is_(True))
        entries = (await self.session.execute(stmt)).scalars().all()

        # Cache plain dicts, not ORM instances: the cache outlives the session that
        # loaded them, and detached instances would raise on attribute access.
        by_surface: dict[str, dict] = {}
        for entry in entries:
            payload = {
                "term": entry.term,
                "scope": str(entry.scope),
                "group_slugs": entry.group_slugs,
                "is_universal": entry.scope == TermScope.UNIVERSAL,
                "dialect": entry.dialect,
                "is_explicit": entry.is_explicit,
                "severity": entry.severity,
                "category": entry.category,
                "never_flag_when": entry.never_flag_when,
            }
            for surface in [entry.term, *(entry.variants or [])]:
                key = self.normalizer.normalize(str(surface))
                if key:
                    by_surface[key] = payload

        pattern = None
        if by_surface:
            # Longest first so "abc def" wins over "abc". (?<!\w)/(?!\w) rather than \b
            # because terms may start or end with non-word characters.
            alternation = "|".join(
                re.escape(s) for s in sorted(by_surface, key=len, reverse=True)
            )
            pattern = re.compile(rf"(?<!\w)(?:{alternation})(?!\w)")

        LexiconMatcher._cache = {"pattern": pattern, "by_surface": by_surface}
        LexiconMatcher._fingerprint = fingerprint
        log.debug("Lexicon index rebuilt", terms=len(by_surface))
        return LexiconMatcher._cache

    @classmethod
    def invalidate_cache(cls) -> None:
        """Call after a pack install so the next scan recompiles."""
        cls._cache = None
        cls._fingerprint = None

    async def scan_text(self, text: str, context_groups: set[str] | None = None) -> list[dict]:
        """Return lexicon hits found in `text`.

        `context_groups` are the target-group slugs present in the surrounding
        context. Each hit carries `in_scope`, which is what the caller should gate on:

        * explicit slurs are always in scope — FR-CL-4 requires explicit hate to be
          flagged regardless of what the post is about
        * universal terms apply to any target
        * group-specific coded terms only count when one of their groups is present,
          so an anti-Yazidi coded term does not fire on a post about Christians
        """
        if not text:
            return []

        index = await self._index()
        pattern = index["pattern"]
        if pattern is None:
            return []

        groups = context_groups or set()
        normalized = self.normalizer.normalize(text)
        hits: list[dict] = []
        seen: set[str] = set()

        for match in pattern.finditer(normalized):
            surface = match.group(0)
            if surface in seen:
                continue
            seen.add(surface)
            entry = index["by_surface"][surface]

            in_scope = (
                entry["is_explicit"]
                or entry["is_universal"]
                or bool(groups & set(entry["group_slugs"]))
            )
            hits.append({**entry, "matched": surface, "in_scope": in_scope})

        if hits:
            log.debug("Lexicon hits found", hits_count=len(hits))
        return hits

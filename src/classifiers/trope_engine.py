"""
Trope Engine — evaluates a context bundle against learned hate-speech tropes.

The rule this enforces is FR-CL-8, and it is the difference between a working system
and an unusable one. A trope surface form is often benign on its own: the pious phrase
"اعوذ بالله من الشيطان الرجيم" is ordinary speech, and only invokes the devil-worship
libel when it appears on Yazidi-related content.

So a surface-form match alone is a *candidate*: it raises review priority and nothing
more. It becomes a *fired* trope only when the activation condition is satisfied by
the bundle. Matching on surface form alone would flag every devout comment on the
platform — for a minority-protection tool, worse than missing hate, because it
silences the community being protected.
"""
from __future__ import annotations

import re

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.normalizer import Normalizer
from src.models.trope_entry import TropeDictionaryEntry

log = structlog.get_logger()

# ponytail: keyword proximity, not a parser. Catches the common "X are not Y" and
# "this is a lie" refutations. Upgrade to dependency parsing only if counter-speech
# false positives show up in the error ledger.
NEGATION_CUES = [
    "ليس", "ليسوا", "ليست", "لا ", "ما ", "مو ", "مب ", "غير صحيح", "افتراء",
    "كذب", "اتهام باطل", "نییە", "نین", "درۆ",
    "not", "isn't", "aren't", "false", "lie",
]
NEGATION_WINDOW = 60  # characters either side of the match


class TropeEngine:
    """Matches a context bundle against trope activation conditions."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.normalizer = Normalizer()

    @staticmethod
    def _negated(haystack: str, position: int) -> bool:
        start = max(0, position - NEGATION_WINDOW)
        window = haystack[start : position + NEGATION_WINDOW]
        return any(cue in window for cue in NEGATION_CUES)

    async def evaluate(self, bundle: dict) -> dict:
        """Evaluate a context bundle.

        `bundle` is the FR-CL-1 context bundle; this reads `comment_text` (falling
        back to `text`) and `target_groups` (slugs present in the surrounding context).

        Returns {"fired": [...], "candidates": [...]}. Only `fired` may contribute to
        an auto-flag; `candidates` raise review priority.
        """
        text = bundle.get("comment_text") or bundle.get("text") or ""
        if not text:
            return {"fired": [], "candidates": []}

        context_groups = set(bundle.get("target_groups") or [])
        if bundle.get("target_group"):
            context_groups.add(bundle["target_group"])

        haystack = self.normalizer.normalize(text)
        post_text = self.normalizer.normalize(bundle.get("parent_post_text") or "")

        stmt = select(TropeDictionaryEntry).where(TropeDictionaryEntry.enabled.is_(True))
        tropes = (await self.session.execute(stmt)).scalars().all()

        fired: list[dict] = []
        candidates: list[dict] = []

        for trope in tropes:
            match = self._first_match(trope, haystack)
            if match is None:
                continue
            surface, position = match

            activation = trope.activation or {}

            # A trope may cover several groups (majority-framing implicatures such as
            # takfiri accusation apply across minorities), so resolve which one the
            # context actually satisfies.
            matched_group = trope.activated_by(context_groups)
            topic_match = any(
                topic and self.normalizer.normalize(topic) in post_text
                for topic in activation.get("post_topic_any") or []
            )

            hit = {
                "trope_id": trope.trope_id,
                "surface_form": surface,
                "target_group": matched_group if matched_group != "*" else None,
                "covers_groups": trope.group_slugs,
                "scope": str(trope.scope),
                "severity": trope.severity,
                "implicature": trope.implicature,
            }

            # FR-CL-8: the activation condition gates firing.
            requires_group = activation.get("requires_target_group", True)
            group_present = matched_group is not None or topic_match

            if requires_group and not group_present:
                hit["reason"] = "surface form matched but no target group in context"
                candidates.append(hit)
                continue

            if activation.get("negation_cancels", True) and self._negated(haystack, position):
                hit["reason"] = "surface form negated or refuted — likely counter-speech"
                candidates.append(hit)
                continue

            if matched_group == "*":
                hit["reason"] = "universal trope: hostile regardless of target group"
            elif matched_group:
                hit["reason"] = f"activation satisfied: content concerns {matched_group}"
            else:
                hit["reason"] = "activation satisfied via post topic"
            fired.append(hit)

        if fired or candidates:
            log.debug("Trope evaluation", fired=len(fired), candidates=len(candidates))

        return {"fired": fired, "candidates": candidates}

    def _first_match(self, trope: TropeDictionaryEntry, haystack: str) -> tuple[str, int] | None:
        """Return the first matching surface form and its position, if any."""
        for form in trope.surface_forms or []:
            raw = form.get("text") if isinstance(form, dict) else form
            if not raw:
                continue
            needle = self.normalizer.normalize(str(raw))
            if not needle:
                continue
            position = haystack.find(needle)
            if position != -1:
                return str(raw), position
        return None

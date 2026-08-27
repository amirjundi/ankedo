"""`never_flag_when` and `self_reference_terms`, made to do something.

Both fields were carried faithfully from the workbook into the database and then read
by nothing. `never_flag_when` reached the specialist's prompt as a line of text the
model could heed or ignore; `self_reference_terms` was written, exported, and never
looked at by any classifier. A curator filling either column was doing work that
changed no outcome.

**What they are for.** A term can be hateful in one mouth and not another. `نسطوري`
in an academic paper, `عبدة الشيطان` quoted by someone rejecting it, a slur used
inside the community that the slur is about — the words are identical and the act is
opposite. Flagging those is not a small error. This system's output becomes a
human-rights record, and the person most likely to quote a libel verbatim is the
person arguing against it. Putting a defender in an evidence file is the specific harm
the project can cause.

**What this does not do: silently clear.** An exemption does not mark an item benign.
It withdraws the *automatic* flag and sends the item to a human, recording why. Two
reasons. The model's own category is one of the signals, and a comment is untrusted
text written by a stranger — letting it argue itself out of review would put the
exemption one prompt injection away from being a bypass. And an exemption is exactly
the interesting case: it is where the dictionary and the context disagree, which is
what a reviewer is for.

**Reclaimed speech needs the group's own words.** `self_reference_terms` is how a
community names itself. Their presence is not proof the speaker is a member — nothing
here can establish that — but their absence is good evidence that they are not, which
is enough to keep the `reclaimed` exemption from firing on an outsider.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.normalizer import Normalizer
from src.models.target_group import TargetGroup

log = structlog.get_logger()

# The vocabulary the workbook's REFERENCE sheet offers in never_flag_when.
COUNTER_SPEECH = "counter_speech"
NEWS_QUOTATION = "news_quotation"
ACADEMIC = "academic"
RECLAIMED = "reclaimed"

# Categories the specialist can return that mean "not hate speech" in themselves.
# REFERENCE marks both NOT hate speech, so a verdict of hate alongside one of these is
# already a contradiction worth a human's attention.
_CATEGORY_SIGNALS = {
    "counter_speech": COUNTER_SPEECH,
    "news_reporting": NEWS_QUOTATION,
    "academic": ACADEMIC,
}

# Signals that arise from the specialist contradicting itself: it returned a verdict of
# hate and a category that REFERENCE defines as not hate speech. That contradiction is
# reason enough to withhold an automatic flag even with no dictionary term involved —
# and the case it covers is the common one. Counter-speech usually quotes a libel that
# no lexicon holds, so the trope fires and no term does; requiring a lexicon hit would
# have left the very scenario the rule exists for uncovered.
#
# `reclaimed` is deliberately not here. It is inferred from the text rather than
# declared by the model, and on its own it says only that a group's own name appears —
# far too little to withdraw a flag from a trope-driven verdict.
_CONTRADICTION_SIGNALS = {COUNTER_SPEECH, NEWS_QUOTATION, ACADEMIC}


@dataclass
class Exemption:
    """Why an automatic flag was withheld."""

    signal: str
    terms: list[str] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {"signal": self.signal, "terms": self.terms, "detail": self.detail}


class ExemptionChecker:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.normalizer = Normalizer()

    async def self_reference_terms(self, slugs: set[str]) -> dict[str, list[str]]:
        """The self-naming terms for the groups a post concerns."""
        if not slugs:
            return {}
        rows = (
            await self.session.execute(
                select(TargetGroup).where(TargetGroup.slug.in_(slugs))
            )
        ).scalars().all()
        return {g.slug: list(g.self_reference_terms or []) for g in rows}

    async def detect_signals(self, *, comment_text: str, target_groups: list[str],
                             specialist: dict | None) -> set[str]:
        """Which never-flag contexts appear to apply to this item."""
        signals: set[str] = set()

        category = ((specialist or {}).get("category") or "").strip().lower()
        if category in _CATEGORY_SIGNALS:
            signals.add(_CATEGORY_SIGNALS[category])

        terms = await self.self_reference_terms(set(target_groups or []))
        if terms and self._contains_any(comment_text, [t for v in terms.values() for t in v]):
            signals.add(RECLAIMED)

        return signals

    def _contains_any(self, text: str, terms: list[str]) -> bool:
        if not text or not terms:
            return False
        haystack = self.normalizer.normalize(text)
        return any(
            needle and needle in haystack
            for needle in (self.normalizer.normalize(t) for t in terms)
        )

    @staticmethod
    def check(lexicon_hits: list[dict], signals: set[str]) -> Exemption | None:
        """Whether the matched terms all excuse themselves in this context.

        Every in-scope hit must list the signal. One term that does not — an
        incitement term that no amount of context excuses — keeps the flag, because
        the exemption is about the *evidence*, not the mood of the sentence. A comment
        that quotes a libel and also says "kill them" is not counter-speech.
        """
        if not signals:
            return None

        in_scope = [h for h in lexicon_hits if h.get("in_scope")]

        if not in_scope:
            # No dictionary term carried this verdict, so there is no never_flag_when
            # rule to consult. A contradiction still counts: the specialist said hate
            # and also said counter-speech, and someone should look at that.
            contradiction = sorted(signals & _CONTRADICTION_SIGNALS)
            if contradiction:
                return Exemption(
                    signal=contradiction[0],
                    terms=[],
                    detail=(
                        f"the verdict is hate but the category is {contradiction[0]!r}, "
                        "which is not hate speech — no dictionary term was involved"
                    ),
                )
            return None

        for signal in sorted(signals):
            covered = [h for h in in_scope if signal in (h.get("never_flag_when") or [])]
            if len(covered) == len(in_scope):
                return Exemption(
                    signal=signal,
                    terms=[h.get("matched") or h.get("term") for h in covered],
                    detail=(
                        f"every matched term lists {signal!r} in never_flag_when, and "
                        f"the context looks like {signal}"
                    ),
                )
        return None

"""Committee orchestration, with the model faked.

These pin the decisions the orchestrator makes *around* the model — the parts that
must hold regardless of what the LLM says, and that would otherwise only be
exercised in production:

* an explicit slur cannot be dropped by a cheap triage model (FR-CL-4)
* a committee disagreement is never silently resolved (FR-CL-11)
* the deterministic layer's findings reach the specialist as evidence

The real-model path needs a Gemini key and is covered by `ankedo classify`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from src.classifiers.committee.schemas import (
    Category,
    CriticDecision,
    SpecialistDecision,
    TriageDecision,
    Verdict,
)
from src.classifiers.context_bundle import ContextBundle
from src.core.settings import get_settings

PACK_DIR = Path(__file__).resolve().parents[2] / "packs" / "iraq-minorities"
PIOUS = "اعوذ بالله من الشيطان الرجيم"


class FakeLLM:
    """Returns canned decisions and records what it was asked."""

    def __init__(self, *, triage=True, specialist=None, critic_agrees=True):
        self.triage_passes = triage
        self.specialist_decision = specialist or SpecialistDecision(
            verdict=Verdict.HATE,
            confidence=0.9,
            category=Category.DEHUMANIZATION,
            target_group="yazidi",
            severity=4,
            relies_on_context=True,
            rationale="invokes the devil-worship trope on Yazidi content",
        )
        self.critic_agrees = critic_agrees
        self.prompts: dict[str, str] = {}

    async def generate(self, *, purpose, prompt, **kwargs):
        self.prompts[purpose] = prompt
        if purpose == "triage":
            return TriageDecision(requires_specialist=self.triage_passes, rationale="fake")
        if purpose == "specialist":
            return self.specialist_decision
        if purpose == "critic":
            return CriticDecision(
                agrees_with_specialist=self.critic_agrees,
                concern=None if self.critic_agrees else "context does not support this",
                suggested_verdict=None if self.critic_agrees else Verdict.BENIGN,
                rationale="fake",
            )
        raise AssertionError(f"unexpected purpose {purpose}")


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import get_session, init_db
    from src.packs.loader import install_pack

    await init_db()
    async with get_session() as s:
        await install_pack(s, PACK_DIR)
        yield s

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


def _orchestrator(session, llm):
    from src.classifiers.committee.orchestrator import CommitteeOrchestrator

    return CommitteeOrchestrator(session, llm=llm)


def _bundle(comment=PIOUS, post="مراسم دينية إيزيدية في معبد لالش", groups=("yazidi",)):
    return ContextBundle(
        comment_text=comment, parent_post_text=post, target_groups=list(groups)
    )


# ------------------------------------------------------------------ happy path


async def test_full_pipeline_flags_contextual_hate(session):
    result = await _orchestrator(session, FakeLLM()).run(_bundle())

    assert result["verdict"] == "hate"
    assert result["hate_speech_flag"] is True
    assert result["relies_on_context"] is True
    assert [t["trope_id"] for t in result["trace"]["tropes_fired"]] == ["yazidi-devil-worship"]


async def test_same_text_without_group_does_not_fire_trope(session):
    """The deterministic half of the acceptance test, independent of the model."""
    llm = FakeLLM(
        specialist=SpecialistDecision(
            verdict=Verdict.BENIGN, confidence=0.95, category=Category.NONE,
            target_group=None, severity=0, relies_on_context=False,
            rationale="ordinary religious phrase on an unrelated post",
        )
    )
    result = await _orchestrator(session, llm).run(
        _bundle(post="شاهدوا هذا الثعبان الضخم في الحديقة", groups=())
    )

    assert result["verdict"] == "benign"
    assert result["trace"]["tropes_fired"] == []
    assert [t["trope_id"] for t in result["trace"]["trope_candidates"]] == ["yazidi-devil-worship"]


# --------------------------------------------------------------- guard rails


async def test_triage_cannot_drop_an_explicit_slur(session):
    """FR-CL-4: explicit hate is flagged regardless of topic.

    Triage runs on the cheapest model; the curated dictionary overrides it.
    """
    from src.models.lexicon_entry import LexiconEntry, TermScope

    session.add(
        LexiconEntry(
            term="عبدة الشيطان", scope=TermScope.UNIVERSAL,
            is_explicit=True, source="test", enabled=True,
        )
    )
    await session.commit()
    from src.classifiers.lexicon import LexiconMatcher

    LexiconMatcher.invalidate_cache()

    llm = FakeLLM(triage=False)  # triage says "not worth looking at"
    result = await _orchestrator(session, llm).run(_bundle(comment="عبدة الشيطان"))

    assert "specialist" in llm.prompts, "explicit slur must reach the specialist anyway"
    assert result["verdict"] == "hate"


async def test_triage_drop_is_honoured_without_explicit_hit(session):
    llm = FakeLLM(triage=False)
    result = await _orchestrator(session, llm).run(_bundle(comment="صباح الخير"))

    assert result["verdict"] == "benign"
    assert "specialist" not in llm.prompts, "a clear item should not cost a specialist call"


async def test_triage_cannot_drop_a_fired_trope(session):
    """A fired trope escalates even when triage says the item is not worth looking at.

    The trope layer exists for hate that carries no dictionary term — mockery, the
    commonest form in the survey data, usually looks like ordinary words. Requiring a
    lexicon hit to survive triage meant the cheapest model had the last word on
    exactly the items tropes were built to catch.
    """
    llm = FakeLLM(triage=False)
    result = await _orchestrator(session, llm).run(_bundle())

    assert [t["trope_id"] for t in result["trace"]["tropes_fired"]] == ["yazidi-devil-worship"]
    assert "specialist" in llm.prompts, "a fired trope must reach the specialist"
    assert result["verdict"] == "hate"


async def test_triage_cannot_drop_a_trope_candidate(session):
    """A candidate is an unresolved question, not a cleared item.

    The pattern matched but its activation topic was not confirmed. Escalating costs
    one specialist call; dropping loses the item with no record that anything matched.
    """
    llm = FakeLLM(
        triage=False,
        specialist=SpecialistDecision(
            verdict=Verdict.BENIGN, confidence=0.9, category=Category.NONE,
            target_group=None, severity=0, relies_on_context=False,
            rationale="ordinary religious phrase on an unrelated post",
        ),
    )
    result = await _orchestrator(session, llm).run(
        _bundle(post="شاهدوا هذا الثعبان الضخم في الحديقة", groups=())
    )

    assert result["trace"]["tropes_fired"] == []
    assert [t["trope_id"] for t in result["trace"]["trope_candidates"]] == ["yazidi-devil-worship"]
    assert "specialist" in llm.prompts, "a trope candidate must reach the specialist"


async def test_disagreement_lowers_confidence_into_the_review_band(session):
    """FR-CL-11: never silently resolve a disagreement — send it to a human."""
    llm = FakeLLM(critic_agrees=False)
    result = await _orchestrator(session, llm).run(_bundle())

    assert result["committee_disagreement"] is True
    assert result["confidence"] <= get_settings().borderline_high
    assert result["trace"]["critic"]["suggested_verdict"] == "benign"


# ---------------------------------------------------------------- prompting


async def test_specialist_receives_deterministic_evidence(session):
    llm = FakeLLM()
    await _orchestrator(session, llm).run(_bundle())

    prompt = llm.prompts["specialist"]
    assert "yazidi-devil-worship" in prompt
    assert "not verdicts" in prompt, "findings must be framed as evidence, not conclusions"


async def test_parent_post_precedes_the_comment_in_the_prompt(session):
    """The relational question is meaningless if the model reads the comment first."""
    llm = FakeLLM()
    await _orchestrator(session, llm).run(_bundle())

    prompt = llm.prompts["specialist"]
    assert prompt.index("PARENT POST") < prompt.index("COMMENT UNDER ASSESSMENT")


async def test_unactivated_trope_is_marked_as_weak_signal(session):
    llm = FakeLLM()
    await _orchestrator(session, llm).run(
        _bundle(post="شاهدوا هذا الثعبان الضخم في الحديقة", groups=())
    )

    prompt = llm.prompts["specialist"]
    assert "NOT activated" in prompt

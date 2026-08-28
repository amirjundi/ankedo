"""The agent must not deny the things it can do.

The operator asked it to test the browser and report some hate speech. It replied
that it "cannot execute a test or use the browser or report, because I only reply
from the conversation and have no authority to perform actual actions", and then said
the operations team or developers do that instead.

Two faults behind one bad answer.

The first was mine. The plain-reply prompt — added to stop the model inventing
statistics — told it that it had not looked anything up and could not see the
database. The model generalised that into having no capabilities at all, and invented
a developer team to hand the work to. A limit meant for one reply became a claim about
the agent.

The second was real: there was no action for what was asked. It could show
configuration and counts, and nothing that exercised the thing the system exists for.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

import src.core.database  # noqa: F401
from src.chat.tools import ACTIONS, ActionError, catalogue, run_action
from src.core.settings import get_settings


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'c.db'}")
    # The committee builds an LLM client in its constructor, so it needs a provider
    # configured before `run` is even reached. No call is made — every test here
    # replaces run itself.
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import get_session, init_db

    await init_db()
    async with get_session() as s:
        yield s

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


# ── the actions exist ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["classify", "test_browser", "collect_now"])
def test_the_missing_actions_are_registered(name):
    assert name in ACTIONS


def test_running_a_collection_needs_a_human():
    """It opens a browser against a live platform under a worker identity. That is
    not something a model decides on its own."""
    assert ACTIONS["collect_now"].mutating is True


def test_classifying_and_testing_the_browser_do_not():
    """Neither changes anything. Requiring confirmation to answer "is the browser
    working" is the friction that makes an operator stop asking."""
    assert ACTIONS["classify"].mutating is False
    assert ACTIONS["test_browser"].mutating is False


def test_the_catalogue_offers_them_to_the_model():
    """The model can only choose what the catalogue names."""
    text = catalogue()

    assert "classify" in text
    assert "test_browser" in text


# ── classify ─────────────────────────────────────────────────────────────────


async def test_classify_needs_text(session):
    with pytest.raises(ActionError) as exc:
        await run_action("classify", session, {"text": "   "})

    assert "text to classify" in str(exc.value)


async def test_classify_reports_the_verdict_and_its_reasoning(session, monkeypatch):
    async def fake_run(self, bundle):
        return {
            "verdict": "hate", "confidence": 0.95, "category": "dehumanization",
            "severity": 4, "hate_speech_flag": True, "committee_disagreement": False,
            "trace": {
                "lexicon_hits": [{"matched": "نجس"}],
                "tropes_fired": [{"trope_id": "ritual-impurity"}],
                "specialist": {"rationale": "aimed at the community in the parent post"},
            },
        }

    monkeypatch.setattr(
        "src.classifiers.committee.orchestrator.CommitteeOrchestrator.run", fake_run
    )

    out = await run_action(
        "classify", session, {"text": "هذوله نجس", "post_text": "مراسم إيزيدية"}
    )

    assert "hate" in out
    assert "0.95" in out
    assert "نجس" in out
    assert "ritual-impurity" in out
    assert "aimed at the community" in out


async def test_classify_says_when_it_had_no_parent_post(session, monkeypatch):
    """A comment is judged against what it replies to. Judging a bare phrase answers
    a different question, and the reply has to say so."""
    async def fake_run(self, bundle):
        return {
            "verdict": "ambiguous", "confidence": 0.5, "category": "none", "severity": 0,
            "hate_speech_flag": False, "committee_disagreement": False, "trace": {},
        }

    monkeypatch.setattr(
        "src.classifiers.committee.orchestrator.CommitteeOrchestrator.run", fake_run
    )

    out = await run_action("classify", session, {"text": "كلام"})

    assert "words alone" in out


async def test_classify_surfaces_a_withheld_flag(session, monkeypatch):
    """An exemption is the interesting case — the operator should see that the flag
    was withheld and why, not a bare 'benign'."""
    async def fake_run(self, bundle):
        return {
            "verdict": "ambiguous", "confidence": 0.6, "category": "counter_speech",
            "severity": 0, "hate_speech_flag": False, "committee_disagreement": False,
            "trace": {"exemption": {"signal": "counter_speech", "terms": []}},
        }

    monkeypatch.setattr(
        "src.classifiers.committee.orchestrator.CommitteeOrchestrator.run", fake_run
    )

    out = await run_action("classify", session, {"text": "x", "post_text": "y"})

    assert "withheld" in out
    assert "counter_speech" in out


# ── test_browser ─────────────────────────────────────────────────────────────


async def test_a_browser_that_will_not_start_says_so_usefully(session, monkeypatch):
    """"It does not work" is not an answer. The reply names the next step."""
    from src.browsers.camoufox_worker import BrowserUnavailable

    async def fails(self):
        raise BrowserUnavailable("official/stable is not installed")

    monkeypatch.setattr(
        "src.browsers.camoufox_worker.CamoufoxWorker.start", fails
    )

    out = await run_action("test_browser", session, {})

    assert "will not start" in out
    assert "repair" in out


async def test_a_working_browser_is_reported_and_closed(session, monkeypatch):
    stopped = []

    async def starts(self):
        return None

    async def stops(self):
        stopped.append(True)

    monkeypatch.setattr("src.browsers.camoufox_worker.CamoufoxWorker.start", starts)
    monkeypatch.setattr("src.browsers.camoufox_worker.CamoufoxWorker.stop", stops)

    out = await run_action("test_browser", session, {})

    assert "started" in out
    assert stopped, "a test that opens a browser and leaves it open is a leak"


# ── a failed turn is still useful ────────────────────────────────────────────


def test_the_fallback_lists_what_can_be_asked_for():
    """"I am not sure what you need" is a dead end, and it is what the operator got
    after asking the agent to test the browser and report some hate speech. By the
    time it is reached, two model attempts have already failed — a third will not
    help. What the operator needs is the list of things they can ask for."""
    from src.chat.agent import what_i_can_do

    text = what_i_can_do()

    for name in ACTIONS:
        assert name in text, f"{name} is not offered when the model gives up"


def test_the_fallback_marks_what_needs_confirming():
    from src.chat.agent import what_i_can_do

    text = what_i_can_do()
    for line in text.splitlines():
        for name, action in ACTIONS.items():
            if line.strip().startswith(f"• {name} "):
                assert ("confirm" in line) == action.mutating, line


def test_the_fallback_is_built_from_the_registry():
    """Written out by hand, it would go stale the first time an action was added and
    nobody remembered the sentence."""
    from src.chat import agent

    source = agent.what_i_can_do.__code__.co_names
    assert "ACTIONS" in source


async def test_a_model_that_says_nothing_still_gets_a_useful_answer(session, monkeypatch):
    """End to end through the agent: both attempts empty, operator still helped."""
    from src.chat.agent import ChatAgent, ChatDecision, PlainReply

    class SaysNothing:
        async def generate(self, *, schema, **kwargs):
            if schema is ChatDecision:
                return ChatDecision(action="reply", message="")
            return PlainReply(message="")

    reply = await ChatAgent(session, llm=SaysNothing()).handle(
        "make a test and use the browser and report some hate speech"
    )

    assert "test_browser" in reply.text
    assert "classify" in reply.text
    assert "not sure what you need" not in reply.text.lower()

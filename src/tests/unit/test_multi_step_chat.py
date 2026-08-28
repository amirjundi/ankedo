"""One message may need more than one action.

`handle` made a single model call, ran a single action, and returned. So "test the
browser and report some hate speech" — two actions — could not be served at all, and
the operator reasonably concluded the agent could do nothing. The actions existed;
nothing could reach more than one of them per message.

That is the difference a tool-calling agent has and this did not. It is closed here
without adopting tool-calling itself: the model still never invokes anything, it names
an action and Python looks the name up in a fixed registry. The content this agent
reads is written by strangers, and a loop that could call arbitrary tools would put a
Facebook comment one prompt injection away from the settings file.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

import src.core.database  # noqa: F401
from src.chat.agent import MAX_STEPS, ChatAgent, ChatDecision, PlainReply
from src.core.settings import get_settings


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
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


class Script:
    """Plays a fixed list of decisions, and records what it was shown."""

    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.prompts: list[str] = []

    async def generate(self, *, schema, prompt="", **kwargs):
        self.prompts.append(prompt)
        if schema is PlainReply:
            return PlainReply(message="")
        if self.decisions:
            return self.decisions.pop(0)
        return ChatDecision(action="reply", message="done")


async def test_two_actions_run_for_one_message(session):
    """The operator's request. Two reads, one answer."""
    llm = Script(
        ChatDecision(action="health"),
        ChatDecision(action="stats", days=7),
        ChatDecision(action="reply", message="Both checked."),
    )

    reply = await ChatAgent(session, llm=llm).handle(
        "check your health and then tell me the stats"
    )

    assert "Both checked." in reply.text
    assert len(llm.prompts) == 3


async def test_the_model_is_shown_what_the_action_returned(session):
    """Without the result fed back, a second step is guesswork rather than a
    continuation."""
    llm = Script(
        ChatDecision(action="stats", days=7),
        ChatDecision(action="reply", message="ok"),
    )

    await ChatAgent(session, llm=llm).handle("stats please")

    assert "[stats]" in llm.prompts[1]
    assert "Actions you have already run" in llm.prompts[1]


async def test_action_output_reaches_the_operator(session):
    """The data, not just the model's prose about it."""
    llm = Script(
        ChatDecision(action="stats", days=7),
        ChatDecision(action="reply", message="That is the picture."),
    )

    reply = await ChatAgent(session, llm=llm).handle("stats")

    assert "0 items collected" in reply.text
    assert "That is the picture." in reply.text


async def test_a_repeated_action_stops_the_loop(session):
    """A model that repeats itself will repeat until the step limit, spending a
    free-tier call each time and telling the operator nothing new."""
    llm = Script(
        ChatDecision(action="stats", days=7),
        ChatDecision(action="stats", days=7),
        ChatDecision(action="stats", days=7),
    )

    reply = await ChatAgent(session, llm=llm).handle("stats")

    assert len(llm.prompts) == 2, "the repeat was not caught"
    assert reply.text


async def test_the_loop_is_bounded(session):
    """A confused model must not spend an afternoon of calls on one sentence."""
    llm = Script(*[ChatDecision(action="stats", days=n) for n in range(20)])

    await ChatAgent(session, llm=llm).handle("go")

    assert len(llm.prompts) <= MAX_STEPS


async def test_a_mutation_stops_the_loop_for_a_human(session):
    """The safety property. Reads may chain; a change waits."""
    llm = Script(
        ChatDecision(action="health"),
        ChatDecision(action="repair", what="browser"),
        ChatDecision(action="reply", message="unreachable"),
    )

    reply = await ChatAgent(session, llm=llm).handle("check and fix the browser")

    assert reply.pending is not None
    assert reply.pending["action"] == "repair"
    assert "Confirm?" in reply.text


async def test_work_already_done_is_shown_with_the_confirmation(session):
    """Confirming should not be a blind choice — the operator sees what the check
    found before agreeing to the repair."""
    llm = Script(
        ChatDecision(action="stats", days=7),
        ChatDecision(action="repair", what="browser"),
    )

    reply = await ChatAgent(session, llm=llm).handle("check then fix")

    assert "0 items collected" in reply.text
    assert "Confirm?" in reply.text


async def test_a_model_failure_mid_loop_keeps_what_was_done(session):
    """The endpoint drops constantly here. Losing a completed browser test and
    reporting only "I could not reach the model" would hide work that actually ran."""
    from src.classifiers.llm_client import LLMError

    class FailsAfterOne:
        def __init__(self):
            self.calls = 0

        async def generate(self, *, schema, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return ChatDecision(action="stats", days=7)
            raise LLMError("connection lost")

    reply = await ChatAgent(session, llm=FailsAfterOne()).handle("stats")

    assert "0 items collected" in reply.text
    assert "could not reach" not in reply.text.lower()


async def test_a_single_action_request_still_takes_one_answer(session):
    """The common case must not become chattier because the loop exists."""
    llm = Script(
        ChatDecision(action="stats", days=7),
        ChatDecision(action="reply", message=""),
    )

    reply = await ChatAgent(session, llm=llm).handle("stats")

    assert reply.text.strip().startswith("Last 7 days")

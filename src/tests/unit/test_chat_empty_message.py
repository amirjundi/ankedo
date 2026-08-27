"""A chosen action with no words is not a failed turn.

Given the seven-field decision schema, a small free model reliably fills in `action`
and leaves `message` empty — it answers the routing question and forgets the talking.
The agent treated that as incomprehension and said "I am not sure what you need",
which is what the operator saw in response to "hello, what do you do?". Measured
against the live proxy: `action='reply'`, `message=''`, three runs out of three.

So the second attempt asks for one field instead of seven. The same model that cannot
reliably populate a compound object answers a single string every time.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from src.chat.agent import ChatAgent, ChatDecision, PlainReply
from src.classifiers.llm_client import LLMError
from src.core.settings import get_settings


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
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


class TwoStepLLM:
    """Empty decision first, then whatever the one-field retry is given."""

    def __init__(self, plain: str | None = "Here is what I do.", *, raises: bool = False):
        self.plain = plain
        self.raises = raises
        self.schemas: list[type] = []

    async def generate(self, *, schema, **kwargs):
        self.schemas.append(schema)
        if schema is ChatDecision:
            return ChatDecision(action="reply", message="")
        if self.raises:
            raise LLMError("endpoint down")
        return PlainReply(message=self.plain or "")


async def test_an_empty_message_is_asked_again_rather_than_shrugged_at(session):
    llm = TwoStepLLM()
    reply = await ChatAgent(session, llm=llm).handle("hello, what do you do?")

    assert reply.text == "Here is what I do."
    assert "not sure" not in reply.text.lower(), "the shrug is back"


async def test_the_retry_asks_for_one_field_not_the_whole_decision(session):
    """The point of the retry is the smaller ask. Requesting ChatDecision again
    would just reproduce the same empty field."""
    llm = TwoStepLLM()
    await ChatAgent(session, llm=llm).handle("hello")

    assert llm.schemas == [ChatDecision, PlainReply]


async def test_a_message_that_is_present_is_used_as_is(session):
    """No second call when the model already answered — it costs tokens and time."""

    class Answers:
        def __init__(self):
            self.calls = 0

        async def generate(self, **kwargs):
            self.calls += 1
            return ChatDecision(action="reply", message="Already said something.")

    llm = Answers()
    reply = await ChatAgent(session, llm=llm).handle("hello")

    assert reply.text == "Already said something."
    assert llm.calls == 1, "asked twice when once was enough"


async def test_the_retry_failing_still_returns_something_sayable(session):
    reply = await ChatAgent(session, llm=TwoStepLLM(raises=True)).handle("hello")

    assert reply.text
    assert "could not" in reply.text.lower()


async def test_a_blank_second_answer_falls_back_to_the_shrug(session):
    """Both attempts empty is genuine incomprehension; say so rather than reply
    with an empty bubble."""
    reply = await ChatAgent(session, llm=TwoStepLLM(plain="   ")).handle("hello")

    assert reply.text.strip()


async def test_the_answer_is_remembered_for_the_next_turn(session):
    """The retry's text is the reply, so it has to enter the history like any other
    — otherwise the conversation has a hole where this turn was."""
    from sqlalchemy import select

    from src.models.chat_message import ChatMessage

    await ChatAgent(session, llm=TwoStepLLM()).handle("hello")

    rows = (await session.execute(select(ChatMessage))).scalars().all()
    said = [r.content for r in rows if r.is_from_agent]
    assert said == ["Here is what I do."]


async def test_the_retry_is_told_it_cannot_see_the_database(session):
    """Measured against the live proxy: asked "how many items did you collect in the
    last 7 days?", the model failed to route to `stats`, fell through to the plain
    retry, and answered "In the last 7 days, I have collected 50 items." The database
    held zero. A fabricated count is worse than a shrug here — this system's output is
    a record of real-world hate speech, and an invented figure in it is a lie with a
    number attached. The retry answers from conversation alone, so it must say so.
    """

    class Captures:
        def __init__(self):
            self.instructions: list[str] = []

        async def generate(self, *, schema, system_instruction=None, **kwargs):
            self.instructions.append(system_instruction or "")
            if schema is ChatDecision:
                return ChatDecision(action="reply", message="")
            return PlainReply(message="ok")

    llm = Captures()
    await ChatAgent(session, llm=llm).handle("how many items did you collect?")

    retry_prompt = llm.instructions[1].lower()
    assert "never state a figure" in retry_prompt
    assert "cannot see the database" in retry_prompt

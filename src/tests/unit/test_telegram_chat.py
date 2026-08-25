"""The Telegram conversational fallback.

The confirmation semantics are the risk here: the operator is on a phone, and a
pending change must never be applied by anything other than an explicit yes.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.chat.agent import ChatReply
from src.chat.channels import telegram_channel as tg


class FakeMessage:
    def __init__(self, text, chat_id=99):
        self.text = text
        self.chat = SimpleNamespace(id=chat_id)
        self.from_user = SimpleNamespace(username="operator")
        self.sent: list[str] = []

    async def answer(self, text):
        self.sent.append(text)


@pytest.fixture(autouse=True)
def authorised(monkeypatch):
    monkeypatch.setattr(tg, "authorized_chat_id", 99)
    tg._pending.clear()
    yield
    tg._pending.clear()


@pytest.fixture
def agent(monkeypatch):
    """Capture what the shared agent was asked, and script what it returns."""
    calls = {"handle": [], "confirm": []}

    class FakeAgent:
        def __init__(self, session):
            pass

        async def handle(self, text):
            calls["handle"].append(text)
            return calls["reply"]

        async def confirm(self, pending):
            calls["confirm"].append(pending)
            return ChatReply(text="applied", action_run="set_config")

    monkeypatch.setattr("src.chat.agent.ChatAgent", FakeAgent)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(tg, "get_session_factory", lambda: (lambda: FakeSession()))
    return calls


async def test_free_text_reaches_the_agent(agent):
    agent["reply"] = ChatReply(text="42 items collected", action_run="stats")
    msg = FakeMessage("how many items today?")

    await tg.handle_conversation(msg)

    assert agent["handle"] == ["how many items today?"]
    assert msg.sent == ["42 items collected"]


async def test_a_mutation_asks_before_applying(agent):
    agent["reply"] = ChatReply(
        text="Change LOG_LEVEL from INFO to DEBUG.", pending={"action": "set_config"}
    )
    msg = FakeMessage("turn on debug logging")

    await tg.handle_conversation(msg)

    assert agent["confirm"] == [], "applied without being asked"
    assert tg._pending[99] == {"action": "set_config"}
    assert "yes" in msg.sent[0].lower()


async def test_yes_applies_the_pending_change(agent):
    tg._pending[99] = {"action": "set_config", "arguments": {"key": "LOG_LEVEL"}}
    msg = FakeMessage("yes")

    await tg.handle_conversation(msg)

    assert agent["confirm"] == [{"action": "set_config", "arguments": {"key": "LOG_LEVEL"}}]
    assert msg.sent == ["applied"]
    assert 99 not in tg._pending


async def test_no_cancels_and_changes_nothing(agent):
    tg._pending[99] = {"action": "set_config"}
    msg = FakeMessage("no")

    await tg.handle_conversation(msg)

    assert agent["confirm"] == []
    assert 99 not in tg._pending
    assert "Cancelled" in msg.sent[0]


async def test_an_unrelated_message_does_not_count_as_consent(agent):
    """The dangerous case: a new question must not apply what was waiting."""
    agent["reply"] = ChatReply(text="ok", action_run=None)
    tg._pending[99] = {"action": "set_config", "arguments": {"key": "LOG_LEVEL"}}
    msg = FakeMessage("actually, how many cases are open?")

    await tg.handle_conversation(msg)

    assert agent["confirm"] == [], "a follow-up question applied a pending change"
    assert 99 not in tg._pending
    assert agent["handle"] == ["actually, how many cases are open?"]


async def test_a_pending_change_is_per_chat(agent):
    agent["reply"] = ChatReply(text="ok")
    tg._pending[99] = {"action": "set_config"}

    await tg.handle_conversation(FakeMessage("yes", chat_id=1234))

    # Chat 1234 is not authorised, so nothing happens and 99 keeps its pending change.
    assert tg._pending[99] == {"action": "set_config"}
    assert agent["confirm"] == []


async def test_an_unauthorised_chat_is_ignored(agent):
    agent["reply"] = ChatReply(text="should not be sent")
    msg = FakeMessage("show me everything", chat_id=4242)

    await tg.handle_conversation(msg)

    assert msg.sent == []
    assert agent["handle"] == []


async def test_a_long_reply_is_split_under_the_telegram_limit(agent):
    agent["reply"] = ChatReply(text="x" * 9000)
    msg = FakeMessage("dump everything")

    await tg.handle_conversation(msg)

    assert len(msg.sent) == 3
    assert all(len(part) <= 4096 for part in msg.sent)


async def test_a_broken_turn_does_not_kill_the_polling_loop(monkeypatch):
    monkeypatch.setattr(tg, "authorized_chat_id", 99)

    class Exploding:
        def __init__(self, session):
            pass

        async def handle(self, text):
            raise RuntimeError("model exploded")

    monkeypatch.setattr("src.chat.agent.ChatAgent", Exploding)

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(tg, "get_session_factory", lambda: (lambda: FakeSession()))
    msg = FakeMessage("hello")

    await tg.handle_conversation(msg)  # must not raise

    assert "went wrong" in msg.sent[0]

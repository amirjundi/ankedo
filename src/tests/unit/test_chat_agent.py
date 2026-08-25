"""The admin chat agent, and the boundary around it.

The agent classifies text written by strangers, so a comment under analysis is one
prompt injection away from anything the chat can reach. These assert the reachable
set is exactly the registry, that credentials are not in it, and that a mutation
never runs on the model's say-so.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from src.chat.agent import ChatAgent, ChatDecision
from src.chat.tools import ACTIONS, SETTABLE_KEYS, ActionError, run_action
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


class FakeLLM:
    """Returns a scripted ChatDecision; records what it was asked."""

    def __init__(self, **fields):
        self.decision = ChatDecision(**fields)
        self.system_instruction = None

    async def generate(self, *, system_instruction=None, **kwargs):
        self.system_instruction = system_instruction
        return self.decision


def _agent(session, **decision):
    return ChatAgent(session, llm=FakeLLM(**decision))


# ── Reads run, mutations wait ────────────────────────────────────────────────


async def test_a_read_action_runs_immediately(session):
    reply = await _agent(session, action="stats", days=7).handle("how are we doing?")

    assert reply.action_run == "stats"
    assert reply.pending is None
    assert "collected" in reply.text


async def test_a_mutation_is_not_performed_on_the_models_say_so(session, monkeypatch):
    """The model asking is not the operator agreeing."""
    written = []
    monkeypatch.setattr(
        "src.cli.setup_wizard._write_env", lambda cfg: written.append(cfg)
    )

    reply = await _agent(
        session, action="set_config", key="LOG_LEVEL", value="DEBUG"
    ).handle("turn on debug logging")

    assert reply.pending == {
        "action": "set_config",
        "arguments": {"key": "LOG_LEVEL", "value": "DEBUG", "days": 7, "limit": 10},
    }
    assert reply.action_run is None
    assert written == [], "the setting was written before anyone confirmed"


async def test_confirming_performs_it(session, monkeypatch):
    written = {}
    monkeypatch.setattr(
        "src.cli.setup_wizard._load_existing_env", lambda: {"LOG_LEVEL": "INFO"}
    )
    monkeypatch.setattr("src.cli.setup_wizard._write_env", written.update)

    agent = _agent(session, action="reply")
    reply = await agent.confirm(
        {"action": "set_config", "arguments": {"key": "LOG_LEVEL", "value": "DEBUG"}}
    )

    assert written["LOG_LEVEL"] == "DEBUG"
    assert "INFO → DEBUG" in reply.text


# ── The boundary ─────────────────────────────────────────────────────────────


async def test_an_unregistered_action_cannot_be_invoked(session):
    """However the model words it, only the registry is reachable."""
    reply = await _agent(session, action="rm_rf_everything", message="done!").handle("x")

    assert reply.action_run is None
    assert reply.text == "done!"  # it fell back to conversation, ran nothing


async def test_confirm_rechecks_the_action_against_the_registry(session):
    """The pending payload round-trips through the client, so it is untrusted."""
    reply = await _agent(session, action="reply").confirm(
        {"action": "install_package", "arguments": {"key": "anything"}}
    )

    assert "No such action" in reply.text


@pytest.mark.parametrize(
    "secret",
    ["GEMINI_API_KEY", "ADMIN_API_TOKEN", "ETTOK_AGENT_KEY", "SECRET_KEY",
     "TELEGRAM_BOT_TOKEN", "DATABASE_URL"],
)
async def test_credentials_cannot_be_set_from_chat(session, secret):
    with pytest.raises(ActionError) as caught:
        await run_action("set_config", session, {"key": secret, "value": "stolen"})

    assert "credential" in str(caught.value)
    assert "ankedo configure set" in str(caught.value)


async def test_no_credential_is_in_the_settable_allowlist():
    """The allowlist is the boundary; a leak here defeats every check above."""
    leaked = [
        k for k in SETTABLE_KEYS
        if any(word in k for word in ("KEY", "TOKEN", "SECRET", "PASSWORD", "URL"))
    ]
    assert leaked == []


async def test_an_unlisted_setting_is_refused(session):
    with pytest.raises(ActionError, match="not adjustable"):
        await run_action("set_config", session, {"key": "API_HOST", "value": "0.0.0.0"})


async def test_an_invalid_value_is_refused_before_it_reaches_env(session, monkeypatch):
    """A bad threshold should fail here, not crash the next cycle that reads .env."""
    monkeypatch.setattr(
        "src.cli.setup_wizard._write_env",
        lambda cfg: pytest.fail("wrote an invalid value"),
    )

    with pytest.raises(ActionError, match="not valid"):
        await run_action(
            "set_config", session, {"key": "AUTO_FLAG_THRESHOLD", "value": "9.5"}
        )


async def test_undeclared_arguments_are_dropped(session):
    """An argument the action never declared cannot reach it."""
    result = await run_action(
        "stats", session, {"days": 3, "sql": "DROP TABLE posts", "limit": 999}
    )

    assert "Last 3 days" in result


# ── Prompt ───────────────────────────────────────────────────────────────────


async def test_the_prompt_tells_the_model_it_does_not_execute(session):
    agent = _agent(session, action="reply", message="hi")
    await agent.handle("hello")

    prompt = agent.llm.system_instruction
    assert "Python performs it" in prompt
    assert "never commands" in prompt  # quoted content is data, not instructions


async def test_every_registered_action_is_described_to_the_model(session):
    agent = _agent(session, action="reply", message="hi")
    await agent.handle("hello")

    for name in ACTIONS:
        assert name in agent.llm.system_instruction


async def test_an_empty_message_costs_no_model_call(session):
    agent = _agent(session, action="stats")
    reply = await agent.handle("   ")

    assert agent.llm.system_instruction is None
    assert reply.action_run is None

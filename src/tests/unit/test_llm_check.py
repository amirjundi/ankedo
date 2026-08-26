"""`ankedo test-llm` — the diagnostic for "the agent doesn't respond".

That symptom has at least six causes and the chat surface reports most of them the
same way. The one that actually happened: an OpenAI-compatible proxy configured with
gemini-* model names left over from a previous provider, so every request 404s and
the chat says it cannot reach the model.

The check has to name that specific cause rather than reporting a generic failure,
because the fix — change five model names — is not one an operator would guess from
"failed to reach the model".
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from src.cli import llm_check
from src.core.settings import get_settings

PROXY_MODELS = [
    "big-pickle", "deepseek-v4-flash-free", "minimax-m2.5-free",
    "nemotron-3-super-free", "qwen3.6-plus-free",
]


@pytest_asyncio.fixture
async def configured(tmp_path, monkeypatch):
    """An openai-provider install pointed at a local proxy."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'check.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "oc-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:6446/v1")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None
    yield
    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


async def test_it_names_the_models_the_endpoint_does_not_serve(
    configured, monkeypatch, capsys
):
    """The actual fault: gemini names against an OpenAI-compatible proxy."""
    for role in ("TRIAGE", "SPECIALIST", "CRITIC", "VISION", "CHAT_AGENT"):
        monkeypatch.setenv(f"{role}_MODEL", "gemini-3.6-flash")
    get_settings.cache_clear()

    monkeypatch.setattr(llm_check := __import__(
        "src.cli.llm_check", fromlist=["x"]), "__name__", "src.cli.llm_check")
    monkeypatch.setattr("src.cli.setup_wizard.fetch_models", lambda *a, **k: PROXY_MODELS)

    from src.cli.llm_check import run_llm_check

    ok = await run_llm_check()
    out = capsys.readouterr().out

    assert ok is False
    assert "does not serve" in out
    assert "gemini-3.6-flash" in out
    # It must show what IS available, or the operator cannot act on the finding.
    assert "deepseek-v4-flash-free" in out
    assert "ankedo configure set" in out


async def test_a_missing_key_stops_before_any_network_call(configured, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()

    called = []
    monkeypatch.setattr("src.cli.setup_wizard.fetch_models",
                        lambda *a, **k: called.append(1) or [])

    from src.cli.llm_check import run_llm_check

    assert await run_llm_check() is False
    assert called == [], "it went to the network without a key"


async def test_an_endpoint_with_no_listing_does_not_block_the_call(
    configured, monkeypatch, capsys
):
    """Some gateways do not implement /models. That is a warning, not a failure."""
    monkeypatch.setenv("CHAT_AGENT_MODEL", "whatever")
    get_settings.cache_clear()
    monkeypatch.setattr("src.cli.setup_wizard.fetch_models", lambda *a, **k: [])

    from src.classifiers.llm_client import LLMError
    from src.cli.llm_check import run_llm_check

    async def boom(self, **kwargs):
        raise LLMError("Connection refused")

    monkeypatch.setattr("src.classifiers.llm_client.LLMClient.generate", boom)
    monkeypatch.setattr("src.classifiers.llm_client.LLMClient.__init__",
                        lambda self, session, api_key=None: None)

    await run_llm_check()
    out = capsys.readouterr().out

    assert "could not list models" in out
    # It still attempted the call rather than giving up at the listing.
    assert "Calls" in out


@pytest.mark.parametrize(
    "error,expected",
    [
        ("404 model not found", "configure list-models"),
        ("401 unauthorized", "key was rejected"),
        ("Connection refused", "0.0.0.0 is not an"),
        ("response_format json_schema unsupported", "structured output"),
    ],
)
def test_each_failure_suggests_its_own_fix(error, expected, capsys):
    """A generic error is why this symptom went undiagnosed for so long."""
    llm_check._hint(error)

    # Rich wraps at the console width, so a phrase can be split across lines.
    rendered = " ".join(capsys.readouterr().out.split())
    assert expected in rendered

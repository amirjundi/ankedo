"""Falling back when a model is rate-limited.

Four of the five models on the deployment proxy answer 429 "free model rate limit"
at any given moment, and the one that works is intermittent — it succeeded and then
failed on the very next call. On a free tier that is the normal condition, not an
incident, so failing an item because one model is busy throws away work another
model could have done.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from pydantic import BaseModel
from sqlalchemy import select

from src.classifiers.llm_client import LLMClient, LLMError, _is_transient
from src.core.settings import get_settings
from src.models.llm_call import LLMCall


class Verdict(BaseModel):
    ok: bool


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'fb.db'}")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("FALLBACK_MODELS", "second-model, third-model")
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


class Backend:
    """Answers per model name, so a test can make one busy and another work."""

    name = "openai"

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.tried: list[str] = []

    async def complete(self, *, model, **kwargs):
        self.tried.append(model)
        outcome = self.behaviour.get(model, "ok")
        if outcome != "ok":
            raise LLMError(outcome)
        return Verdict(ok=True), 7, 3


def _client(session, backend):
    client = LLMClient.__new__(LLMClient)
    client.session = session
    client.settings = get_settings()
    client._backend = backend
    return client


async def _run(client):
    return await client.generate(
        model="primary", prompt="p", schema=Verdict,
        purpose="triage", prompt_version="v1",
    )


# ── What counts as worth retrying ────────────────────────────────────────────


@pytest.mark.parametrize("error", [
    "429 rate limit exceeded",
    "Model is unavailable. (free model rate limit)",
    "Request timed out.",
    "503 Service Unavailable",
    "upstream overloaded",
    "Connection refused",
])
def test_transient_failures_are_recognised(error):
    assert _is_transient(error)


@pytest.mark.parametrize("error", [
    "response did not match Verdict",
    "model refused: I can't help with that",
    "invalid api key",
])
def test_permanent_failures_are_not(error):
    """These fail identically on every model; moving on only wastes the budget."""
    assert not _is_transient(error)


# ── Behaviour ────────────────────────────────────────────────────────────────


async def test_a_rate_limited_model_falls_through_to_a_working_one(session):
    backend = Backend({"primary": "429 free model rate limit"})
    client = _client(session, backend)

    result = await _run(client)

    assert result.ok is True
    assert backend.tried == ["primary", "second-model"]


async def test_it_keeps_going_until_one_answers(session):
    backend = Backend({
        "primary": "429 rate limit",
        "second-model": "Request timed out.",
    })
    client = _client(session, backend)

    assert (await _run(client)).ok is True
    assert backend.tried == ["primary", "second-model", "third-model"]


async def test_a_permanent_failure_does_not_burn_the_other_models(session):
    """A schema mismatch will happen identically on every candidate."""
    backend = Backend({m: "response did not match Verdict"
                       for m in ("primary", "second-model", "third-model")})
    client = _client(session, backend)

    with pytest.raises(LLMError):
        await _run(client)

    assert backend.tried == ["primary"]


async def test_exhausting_every_model_names_what_was_tried(session):
    backend = Backend({m: "429 rate limit"
                       for m in ("primary", "second-model", "third-model")})
    client = _client(session, backend)

    with pytest.raises(LLMError) as caught:
        await _run(client)

    message = str(caught.value)
    assert "third-model" in message
    assert "tried" in message


async def test_the_ledger_records_the_model_that_actually_answered(session):
    """Cost and reproducibility both hang off knowing which model ran."""
    backend = Backend({"primary": "429 rate limit"})
    client = _client(session, backend)

    await _run(client)

    call = (await session.execute(select(LLMCall))).scalars().all()[-1]
    assert call.model == "second-model"
    assert call.succeeded is True


async def test_no_fallbacks_configured_still_works(session, monkeypatch):
    monkeypatch.setenv("FALLBACK_MODELS", "")
    get_settings.cache_clear()

    backend = Backend({})
    client = _client(session, backend)

    assert (await _run(client)).ok is True
    assert backend.tried == ["primary"]

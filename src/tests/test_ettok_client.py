"""Client and lexicon-sync behaviour against a mocked platform.

The contract rules worth pinning down: auth failures stop rather than retry, server
errors do retry, and a free-text upstream target_group that does not resolve is
reported instead of silently dropped.
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from src.core.settings import get_settings

PACK_DIR = __import__("pathlib").Path(__file__).resolve().parents[2] / "packs" / "iraq-minorities"


@pytest.fixture(autouse=True)
def platform_env(monkeypatch):
    """Every test in this module needs a configured client."""
    monkeypatch.setenv("ETTOK_BASE_URL", "https://example.test/api/hermes/")
    monkeypatch.setenv("ETTOK_AGENT_KEY", "test-key")
    monkeypatch.setenv("ETTOK_MAX_RETRIES", "1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
        await install_pack(s, PACK_DIR)  # taxonomy, so groups can resolve
        yield s

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


def _client(handler):
    from src.ettok.client import EttokClient

    return EttokClient(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ auth rules


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failures_do_not_retry(status):
    """Contract: stop and alert. Retrying a revoked key cannot help."""
    from src.ettok.client import AgentKeyRejected

    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status)

    async with _client(handler) as client:
        with pytest.raises(AgentKeyRejected):
            await client.heartbeat()

    assert len(calls) == 1, "auth failure must not be retried"


async def test_server_errors_are_retried():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"agent_id": "x", "scan_requested": False})

    async with _client(handler) as client:
        assert await client.heartbeat() == {"agent_id": "x", "scan_requested": False}

    assert len(calls) == 2, "transient server errors should be retried"


async def test_auth_headers_are_sent():
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        await client.heartbeat()

    assert seen["authorization"] == "Bearer test-key"
    assert "x-agent-id" in seen


async def test_missing_key_is_refused_up_front(monkeypatch):
    from src.ettok.client import EttokClient, EttokError

    monkeypatch.setenv("ETTOK_AGENT_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(EttokError, match="no agent key"):
        EttokClient()
    get_settings.cache_clear()


# ------------------------------------------------------------- lexicon sync


def _lexicon_response(terms):
    def handler(request):
        assert request.url.path.endswith("/lexicon/")
        return httpx.Response(200, json={"terms": terms, "total": len(terms)})

    return handler


async def test_sync_resolves_free_text_groups(session):
    """Upstream target_group is free text; it has to land on a canonical group."""
    from src.ettok.sync import sync_lexicon

    handler = _lexicon_response(
        [
            {"id": 12, "term": "عبدة الشيطان", "language": "ar",
             "category": "dehumanization", "target_group": "Yazidi",
             "severity_weight": 8, "is_regex": False},
        ]
    )
    async with _client(handler) as client:
        result = await sync_lexicon(session, client)

    assert result.created == 1
    assert result.unresolved_groups == {}

    from sqlalchemy import select

    from src.models.lexicon_entry import LexiconEntry

    row = (await session.execute(select(LexiconEntry))).scalars().one()
    assert row.platform_id == 12
    assert row.group_slugs == ["yazidi"], "free-text 'Yazidi' must resolve to the slug"


async def test_unresolved_group_is_reported_not_dropped(session):
    """A group that does not resolve means terms that can never match their tropes."""
    from src.ettok.sync import sync_lexicon

    handler = _lexicon_response(
        [{"id": 5, "term": "x", "language": "ar", "category": "slur",
          "target_group": "Martians", "severity_weight": 3, "is_regex": False}]
    )
    async with _client(handler) as client:
        result = await sync_lexicon(session, client)

    assert result.unresolved_groups == {"Martians": 1}
    assert result.created == 1, "the term is still cached, just ungated"


async def test_uncompilable_regex_is_skipped_not_fatal(session):
    """Contract: skip the entry rather than aborting the scan."""
    from src.ettok.sync import sync_lexicon

    handler = _lexicon_response(
        [
            {"id": 1, "term": "[unclosed", "language": "ar", "category": "slur",
             "target_group": "Yazidi", "severity_weight": 5, "is_regex": True},
            {"id": 2, "term": "fine", "language": "ar", "category": "slur",
             "target_group": "Yazidi", "severity_weight": 5, "is_regex": False},
        ]
    )
    async with _client(handler) as client:
        result = await sync_lexicon(session, client)

    assert len(result.bad_regexes) == 1
    assert result.created == 1, "the good term still imports"


async def test_removed_terms_are_deactivated_not_deleted(session):
    """A bad sync must be recoverable, and the audit trail has to survive."""
    from sqlalchemy import select

    from src.ettok.sync import sync_lexicon
    from src.models.lexicon_entry import LexiconEntry

    term = {"id": 7, "term": "gone", "language": "ar", "category": "slur",
            "target_group": "Yazidi", "severity_weight": 5, "is_regex": False}

    async with _client(_lexicon_response([term])) as client:
        await sync_lexicon(session, client)
    async with _client(_lexicon_response([])) as client:
        result = await sync_lexicon(session, client)

    assert result.deactivated == 1
    row = (await session.execute(select(LexiconEntry))).scalars().one()
    assert row.enabled is False and row.platform_id == 7


async def test_sync_is_idempotent(session):
    from sqlalchemy import func, select

    from src.ettok.sync import sync_lexicon
    from src.models.lexicon_entry import LexiconEntry

    terms = [{"id": 3, "term": "t", "language": "ar", "category": "slur",
              "target_group": "Yazidi", "severity_weight": 5, "is_regex": False}]

    async with _client(_lexicon_response(terms)) as client:
        await sync_lexicon(session, client)
        second = await sync_lexicon(session, client)

    assert second.created == 0 and second.updated == 1
    count = (await session.execute(select(func.count(LexiconEntry.id)))).scalar_one()
    assert count == 1

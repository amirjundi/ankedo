"""Transport security and delivery reliability.

Three properties, each protecting something specific:

* **TLS is mandatory** — the payload is evidence about people who are already targets
  of violence, plus a token granting submission rights, crossing residential WiFi.
* **Nothing is lost** — a dropped connection must cost a delay, not a classification.
  Re-collecting means re-scraping content that may have been deleted since.
* **Nothing is duplicated** — a retry after a lost response must not create a second
  report about the same person.
"""
from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from src.core.settings import get_settings

TOKEN = "agent-key-abcdef123456"


@pytest.fixture(autouse=True)
def platform_env(monkeypatch):
    monkeypatch.setenv("ETTOK_BASE_URL", "https://example.test/api/hermes/")
    monkeypatch.setenv("ETTOK_AGENT_KEY", TOKEN)
    monkeypatch.setenv("ETTOK_MAX_RETRIES", "2")
    # These tests exercise transport mechanics — retries, idempotency, the failure
    # ladder — using VERDICT as the carrier. The verdict gate is a separate concern
    # with its own tests in test_outbox_wiring.py; held items would never reach the
    # transport under test.
    monkeypatch.setenv("ETTOK_VERDICT_ENDPOINT_READY", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(handler):
    from src.ettok.client import EttokClient

    return EttokClient(transport=httpx.MockTransport(handler))


# ----------------------------------------------------------------------- TLS


def test_plaintext_http_is_refused(monkeypatch):
    """A silent downgrade would put evidence and the token on the wire in the clear."""
    from src.ettok.client import EttokClient, EttokError

    monkeypatch.setenv("ETTOK_BASE_URL", "http://ettok.net/api/hermes/")
    get_settings.cache_clear()

    with pytest.raises(EttokError, match="plaintext"):
        EttokClient()


def test_localhost_http_is_allowed(monkeypatch):
    """Development against a local server, where there is no network to sniff."""
    from src.ettok.client import EttokClient

    monkeypatch.setenv("ETTOK_BASE_URL", "http://127.0.0.1:8000/api/hermes/")
    get_settings.cache_clear()
    EttokClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))


def test_https_is_accepted():
    from src.ettok.client import EttokClient

    EttokClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))


# --------------------------------------------------------------- idempotency


async def test_submissions_carry_an_idempotency_key():
    seen = []

    def handler(request):
        seen.append(request.headers.get("idempotency-key"))
        return httpx.Response(200, json={"accepted": 1})

    async with _client(handler) as client:
        await client.post_flagged_items([{"text": "x"}], request_id="req-123")

    assert seen == ["req-123"]


async def test_a_retry_reuses_the_same_key():
    """The case this exists for: the server processed it, the response was lost."""
    seen = []

    def handler(request):
        seen.append(request.headers.get("idempotency-key"))
        if len(seen) < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"accepted": 1})

    async with _client(handler) as client:
        await client.post_flagged_items([{"text": "x"}], request_id="req-abc")

    assert seen == ["req-abc"] * 3, "every attempt must carry the same key"


async def test_reads_do_not_send_an_idempotency_key():
    seen = []

    def handler(request):
        seen.append(request.headers.get("idempotency-key"))
        return httpx.Response(200, json={"terms": []})

    async with _client(handler) as client:
        await client.get_lexicon()

    assert seen == [None]


# -------------------------------------------------------------------- outbox


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'ob.db'}")
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


async def test_queued_work_survives_a_failed_send(session):
    """A dropped line costs a delay, not the classification."""
    from src.ettok import outbox
    from src.models.outbox import OutboxKind, OutboxStatus

    await outbox.enqueue(session, OutboxKind.VERDICT, {"items": [{"text": "x"}]})
    await session.commit()

    async with _client(lambda r: httpx.Response(503)) as client:
        result = await outbox.drain(session, client)

    assert result["sent"] == 0
    still_queued = await outbox.pending(session)
    assert len(still_queued) == 1
    assert still_queued[0].status == OutboxStatus.PENDING


async def test_a_successful_send_clears_the_item(session):
    from src.ettok import outbox
    from src.models.outbox import OutboxKind

    await outbox.enqueue(session, OutboxKind.VERDICT, {"items": [{"text": "x"}]})
    await session.commit()

    async with _client(lambda r: httpx.Response(200, json={"accepted": 1})) as client:
        result = await outbox.drain(session, client)

    assert result["sent"] == 1
    assert await outbox.pending(session) == []


async def test_the_same_request_id_is_reused_across_drains(session):
    """Two drain attempts on one item must look like one submission to the server."""
    from src.ettok import outbox
    from src.models.outbox import OutboxKind

    item = await outbox.enqueue(session, OutboxKind.VERDICT, {"items": [{"text": "x"}]})
    original = item.request_id
    await session.commit()

    seen = []

    def handler(request):
        seen.append(request.headers.get("idempotency-key"))
        return httpx.Response(503)

    async with _client(handler) as client:
        await outbox.drain(session, client)
    async with _client(handler) as client:
        await outbox.drain(session, client)

    assert len(set(seen)) == 1
    assert seen[0] == original


async def test_a_permanently_failing_item_stops_blocking_the_queue(session):
    from src.ettok import outbox
    from src.models.outbox import OutboxKind, OutboxStatus

    item = await outbox.enqueue(session, OutboxKind.VERDICT, {"items": [{"text": "x"}]})
    item.attempts = outbox.MAX_ATTEMPTS - 1
    await session.commit()

    async with _client(lambda r: httpx.Response(503)) as client:
        await outbox.drain(session, client)

    assert item.status == OutboxStatus.FAILED
    assert await outbox.pending(session) == [], "must not retry forever"


async def test_a_failed_item_is_kept_not_deleted(session):
    """Evidence of a contract mismatch. Dropping it would hide that work is being lost."""
    from sqlalchemy import select

    from src.ettok import outbox
    from src.models.outbox import OutboxItem, OutboxKind

    item = await outbox.enqueue(session, OutboxKind.VERDICT, {"items": [{"text": "x"}]})
    item.attempts = outbox.MAX_ATTEMPTS - 1
    await session.commit()

    async with _client(lambda r: httpx.Response(503)) as client:
        await outbox.drain(session, client)

    rows = (await session.execute(select(OutboxItem))).scalars().all()
    assert len(rows) == 1
    assert rows[0].last_error


async def test_a_revoked_key_stops_the_drain_without_spending_attempts(session):
    """A revoked key is not the items' fault — burning their budget would lose them."""
    from src.ettok import outbox
    from src.ettok.client import AgentKeyRejected
    from src.models.outbox import OutboxKind

    item = await outbox.enqueue(session, OutboxKind.VERDICT, {"items": [{"text": "x"}]})
    await session.commit()

    async with _client(lambda r: httpx.Response(401)) as client:
        with pytest.raises(AgentKeyRejected):
            await outbox.drain(session, client)

    assert item.attempts == 0
    assert len(await outbox.pending(session)) == 1


async def test_depth_reports_what_is_waiting(session):
    """A rising pending count is how an operator notices submissions are not landing."""
    from src.ettok import outbox
    from src.models.outbox import OutboxKind

    for _ in range(3):
        await outbox.enqueue(session, OutboxKind.VERDICT, {"items": []})
    await session.commit()

    assert (await outbox.depth(session))["pending"] == 3

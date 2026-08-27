"""The road from a verdict to the platform, and the gate across it.

Every piece of this existed and none of them were connected: `build_verdict`
assembled an item, the outbox stored and retried one, `drain` sent one — and not one
had a caller. Classification wrote its result to the local database and stopped there.

These tests fasten the junction shut. The first three fail if classification stops
queueing; the rest fail if verdicts start leaving before the platform can store them.

On that gate: `POST flagged-items/` is live and returns 200, but it is still the
prefilter endpoint — no columns for verdict, severity, confidence, rationale or
versions, so it accepts the payload and drops those fields, then re-classifies the
item with its own model. A 200 that discards the content is the worst possible
answer, because nothing downstream can tell it happened. So verdicts wait in the
outbox, which is the situation the outbox was built for.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.core.settings import get_settings
from src.ettok.outbox import drain, enqueue, pending
from src.ettok.queue_verdicts import is_submittable, queue_verdicts
from src.models.outbox import OutboxItem, OutboxKind, OutboxStatus


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


# ── what counts as submittable ───────────────────────────────────────────────


def test_a_flagged_item_is_submitted():
    assert is_submittable({"hate_speech_flag": True})


def test_an_item_the_agent_declined_to_resolve_is_submitted():
    """`ambiguous` and `committee_disagreement` both mean the agent chose not to
    decide. Those are the cases that most need a human, and filtering on the flag
    alone would drop exactly them."""
    assert is_submittable({"hate_speech_flag": False, "verdict": "ambiguous"})
    assert is_submittable({"hate_speech_flag": False, "committee_disagreement": True})


def test_a_cleared_item_is_not_submitted():
    """The platform never receives what was read and found benign — only the count,
    which travels in the scan log. Uploading every comment read would build a
    surveillance archive of ordinary people."""
    assert not is_submittable(
        {"hate_speech_flag": False, "verdict": "not_hate", "committee_disagreement": False}
    )


# ── queueing ─────────────────────────────────────────────────────────────────


async def test_verdicts_land_in_the_outbox(session):
    await queue_verdicts(session, [{"content": "x"}, {"content": "y"}])
    await session.commit()

    rows = (await session.execute(select(OutboxItem))).scalars().all()
    assert len(rows) == 1, "one row per post, not one per comment"
    assert rows[0].kind == OutboxKind.VERDICT
    assert len(rows[0].payload["items"]) == 2
    assert rows[0].status == OutboxStatus.PENDING


async def test_nothing_is_queued_when_nothing_was_submittable(session):
    await queue_verdicts(session, [])
    await session.commit()

    assert (await session.execute(select(OutboxItem))).scalars().all() == []


async def test_every_row_carries_a_distinct_idempotency_key(session):
    await queue_verdicts(session, [{"content": "a"}])
    await queue_verdicts(session, [{"content": "b"}])
    await session.commit()

    rows = (await session.execute(select(OutboxItem))).scalars().all()
    assert len({r.request_id for r in rows}) == 2


# ── the gate ─────────────────────────────────────────────────────────────────


class RecordingClient:
    def __init__(self):
        self.verdict_calls = 0
        self.gap_calls = 0

    async def post_flagged_items(self, items, *, request_id=None):
        self.verdict_calls += 1
        return {"accepted": len(items)}

    async def post_lexicon_gaps(self, gaps):
        self.gap_calls += 1
        return {"accepted": len(gaps)}

    async def post_scan_log(self, payload):
        return {}


async def test_verdicts_are_not_sent_while_the_platform_cannot_store_them(session, monkeypatch):
    monkeypatch.setenv("ETTOK_VERDICT_ENDPOINT_READY", "false")
    get_settings.cache_clear()

    await queue_verdicts(session, [{"content": "x"}])
    await session.commit()

    client = RecordingClient()
    result = await drain(session, client)

    assert client.verdict_calls == 0, "sent into an endpoint that would discard it"
    assert result["held"] == 1


async def test_a_held_verdict_keeps_its_full_retry_budget(session, monkeypatch):
    """Held is not failed. Spending attempts against an endpoint that was never
    offered the item would exhaust the budget before the real endpoint exists."""
    monkeypatch.setenv("ETTOK_VERDICT_ENDPOINT_READY", "false")
    get_settings.cache_clear()

    await queue_verdicts(session, [{"content": "x"}])
    await session.commit()

    for _ in range(3):
        await drain(session, RecordingClient())

    row = (await session.execute(select(OutboxItem))).scalar_one()
    assert row.attempts == 0
    assert row.status == OutboxStatus.PENDING
    assert row.last_error is None


async def test_the_backlog_ships_once_the_platform_is_ready(session, monkeypatch):
    """The point of holding rather than dropping: flipping one setting releases
    everything that accumulated, with nothing lost."""
    monkeypatch.setenv("ETTOK_VERDICT_ENDPOINT_READY", "false")
    get_settings.cache_clear()

    for i in range(3):
        await queue_verdicts(session, [{"content": str(i)}])
    await session.commit()
    await drain(session, RecordingClient())

    monkeypatch.setenv("ETTOK_VERDICT_ENDPOINT_READY", "true")
    get_settings.cache_clear()

    client = RecordingClient()
    result = await drain(session, client)

    assert client.verdict_calls == 3
    assert result["sent"] == 3
    assert await pending(session) == []


async def test_other_kinds_are_unaffected_by_the_verdict_gate(session, monkeypatch):
    """`lexicon-gaps/` is live and stores what it is sent. Holding verdicts must not
    hold anything else."""
    monkeypatch.setenv("ETTOK_VERDICT_ENDPOINT_READY", "false")
    get_settings.cache_clear()

    await enqueue(session, OutboxKind.LEXICON_GAP, {"gaps": [{"term": "x"}]})
    await session.commit()

    client = RecordingClient()
    result = await drain(session, client)

    assert client.gap_calls == 1
    assert result["sent"] == 1

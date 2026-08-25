"""Collection scheduling and the counts it produces.

The due-ness logic decides how hard the agent hits each platform, so getting it wrong
costs accounts. `comments_scanned` is tested because it exists nowhere else — the
platform only ever sees flagged items, never the denominator.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest_asyncio

from src.core.settings import get_settings
from src.models.agent_worker_account import AccountStage, AccountState, AgentWorkerAccount
from src.models.tracked_account import AccountStatus, TrackedAccount


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
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


def _account(handle="page", *, last_crawled=None, interval=300, priority=0, status=AccountStatus.ACTIVE):
    return TrackedAccount(
        platform="instagram",
        handle=handle,
        page_url=f"https://instagram.com/{handle}/",
        status=status,
        crawl_interval_seconds=interval,
        priority=priority,
        last_crawled_at=last_crawled.isoformat() if last_crawled else None,
    )


def _runner(session):
    from src.core.collection_runner import CollectionRunner

    return CollectionRunner(session)


async def test_never_crawled_accounts_are_due(session):
    session.add(_account())
    await session.commit()

    due = await _runner(session)._due_accounts()
    assert [a.handle for a in due] == ["page"]


async def test_recently_crawled_accounts_are_skipped(session):
    recent = datetime.now(timezone.utc) - timedelta(seconds=60)
    session.add(_account(last_crawled=recent, interval=300))
    await session.commit()

    assert await _runner(session)._due_accounts() == []


async def test_accounts_become_due_once_their_own_interval_elapses(session):
    stale = datetime.now(timezone.utc) - timedelta(seconds=400)
    session.add(_account("slow", last_crawled=stale, interval=300))
    session.add(_account("fast", last_crawled=stale, interval=900))
    await session.commit()

    due = await _runner(session)._due_accounts()
    assert [a.handle for a in due] == ["slow"], "interval is per account, not global"


async def test_higher_priority_is_collected_first(session):
    session.add(_account("low", priority=0))
    session.add(_account("high", priority=10))
    await session.commit()

    due = await _runner(session)._due_accounts()
    assert [a.handle for a in due] == ["high", "low"]


async def test_banned_accounts_are_never_crawled(session):
    session.add(_account("banned", status=AccountStatus.BANNED))
    await session.commit()

    assert await _runner(session)._due_accounts() == []


async def test_accounts_without_a_url_are_skipped(session):
    account = _account("nourl")
    account.page_url = None
    session.add(account)
    await session.commit()

    assert await _runner(session)._due_accounts() == []


async def test_limit_caps_a_single_pass(session):
    for i in range(5):
        session.add(_account(f"page{i}"))
    await session.commit()

    assert len(await _runner(session)._due_accounts(limit=2)) == 2


# ------------------------------------------------------- worker selection


def _worker(username, *, last_used=None, state=AccountState.HEALTHY, stage=AccountStage.ACTIVE):
    return AgentWorkerAccount(
        platform="instagram",
        username=username,
        password_encrypted="x",
        fingerprint_seed="x",
        state=state,
        stage=stage,
        last_used_at=last_used.isoformat() if last_used else None,
    )


async def test_least_recently_used_worker_is_chosen(session):
    """FR-AC-3: spread load rather than concentrating it on one identity."""
    older = datetime.now(timezone.utc) - timedelta(hours=5)
    newer = datetime.now(timezone.utc) - timedelta(minutes=5)
    session.add(_worker("recent", last_used=newer))
    session.add(_worker("rested", last_used=older))
    await session.commit()

    chosen = await _runner(session)._worker_for("instagram")
    assert chosen.username == "rested"


async def test_blocked_workers_are_not_used(session):
    session.add(_worker("blocked", state=AccountState.BLOCKED))
    await session.commit()

    assert await _runner(session)._worker_for("instagram") is None


async def test_quarantined_workers_are_not_used(session):
    session.add(_worker("quarantined", stage=AccountStage.QUARANTINE))
    await session.commit()

    assert await _runner(session)._worker_for("instagram") is None


async def test_no_worker_for_an_unmonitored_platform(session):
    session.add(_worker("ig"))
    await session.commit()

    assert await _runner(session)._worker_for("tiktok") is None


async def test_pass_with_no_due_accounts_is_a_no_op(session):
    stats = await _runner(session).run()
    assert stats.accounts_attempted == 0
    assert stats.comments_scanned == 0


async def test_missing_worker_account_is_reported_not_silent(session):
    """A platform with no healthy identity must surface, not quietly collect nothing."""
    session.add(_account())
    await session.commit()

    stats = await _runner(session).run()
    assert any("no healthy worker account" in e for e in stats.errors)


def test_stats_track_per_platform_denominators():
    """Contract amendment §1 — hate density needs what was scanned, not just flagged."""
    from src.core.collection_runner import CollectionStats

    stats = CollectionStats()
    stats.bump("facebook", "comments_scanned", 40)
    stats.bump("instagram", "comments_scanned", 12)
    assert stats.per_platform["facebook"]["comments_scanned"] == 40
    assert stats.per_platform["instagram"]["comments_scanned"] == 12

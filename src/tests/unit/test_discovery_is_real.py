"""Autonomous discovery must be based on something that happened.

`high_hate_density = True  # Stub`. Hardcoded. Every cycle the engine added a handle
called `stub_discovered_user` to the watch list and wrote an autonomous decision
recording `recent_flags: 4` — a number nothing had counted — into the decision log
that NFR-AU-2 exists to make trustworthy.

The operator watched it happen in their own terminal: three notifications a cycle,
forty-five accumulated timeouts, and a watch list containing a name that does not
exist. On a system that had collected nothing.

A fabricated audit trail is worse than none. The value of a decision log is that
someone can later ask why the agent watched a particular person, and this one answered
with a number it invented.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

import src.core.database  # noqa: F401  — registers every model with the mapper
from src.core.settings import get_settings
from src.models.post import Post, QueueState
from src.models.tracked_account import AccountSource, AccountStatus, TrackedAccount


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'd.db'}")
    monkeypatch.setenv("DISCOVERY_FLAG_THRESHOLD", "3")
    monkeypatch.setenv("DISCOVERY_WINDOW_DAYS", "14")
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


async def _account(session, handle="watched", platform="facebook"):
    account = TrackedAccount(
        platform=platform, handle=handle, status=AccountStatus.ACTIVE,
        source=AccountSource.MANUAL,
    )
    session.add(account)
    await session.flush()
    return account


async def _flagged(session, account, author, count, *, days_ago=1, flag=True):
    for i in range(count):
        post = Post(
            tracked_account_id=account.id, platform=account.platform,
            platform_post_id=f"{author}-{i}-{days_ago}", url=f"https://x/{author}/{i}/{days_ago}",
            content_text="نص", author_name=author, hate_speech_flag=flag,
            queue_state=QueueState.DONE,
            created_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        )
        session.add(post)
    await session.commit()


def _engine(session):
    from src.core.discovery_engine import DiscoveryEngine

    return DiscoveryEngine(session)


# ── nothing collected means nothing discovered ───────────────────────────────


async def test_an_empty_database_discovers_nothing(session):
    """The whole bug. This used to add stub_discovered_user and log a decision."""
    engine = _engine(session)

    assert await engine._authors_over_threshold() == []

    await engine.run_discovery()

    accounts = (await session.execute(select(TrackedAccount))).scalars().all()
    assert accounts == [], "discovery invented an account out of an empty database"


async def test_nothing_named_stub_is_ever_written(session):
    """Belt and braces on the specific name the operator saw in their watch list."""
    await _engine(session).run_discovery()

    handles = [
        a.handle for a in (await session.execute(select(TrackedAccount))).scalars()
    ]
    assert not any("stub" in h for h in handles)


# ── real data, real threshold ────────────────────────────────────────────────


async def test_an_author_over_the_threshold_is_found(session):
    account = await _account(session)
    await _flagged(session, account, "repeat_offender", 4)

    found = await _engine(session)._authors_over_threshold()

    assert [(p, h, f) for p, h, f in found] == [("facebook", "repeat_offender", 4)]


async def test_an_author_under_the_threshold_is_not(session):
    """Two flagged items is not a pattern. The threshold exists so the agent does not
    start watching someone on the strength of a bad afternoon."""
    account = await _account(session)
    await _flagged(session, account, "twice_only", 2)

    assert await _engine(session)._authors_over_threshold() == []


async def test_unflagged_posts_do_not_count(session):
    account = await _account(session)
    await _flagged(session, account, "prolific_but_fine", 10, flag=False)

    assert await _engine(session)._authors_over_threshold() == []


async def test_old_items_fall_outside_the_window(session):
    """Concentration is the concern, not a lifetime total. Three flags spread over a
    year is not the same as three in a week, and treating them alike turns an old
    record into a permanent one."""
    account = await _account(session)
    await _flagged(session, account, "long_ago", 5, days_ago=400)

    assert await _engine(session)._authors_over_threshold() == []


async def test_an_already_tracked_account_is_not_rediscovered(session):
    """It was re-logging the same decision every cycle, which is how a decision log
    stops being read."""
    account = await _account(session, handle="already_watched")
    await _flagged(session, account, "already_watched", 5)

    assert await _engine(session)._authors_over_threshold() == []


# ── what gets written ────────────────────────────────────────────────────────


async def test_a_discovered_author_is_added_to_the_watch_list(session):
    account = await _account(session)
    await _flagged(session, account, "new_offender", 4)

    await _engine(session).run_discovery()

    handles = {
        a.handle for a in (await session.execute(select(TrackedAccount))).scalars()
    }
    assert "new_offender" in handles


async def test_the_recorded_count_is_the_counted_one(session):
    """`recent_flags: 4` used to be a literal. Whatever number reaches the decision
    log now has to be one something counted, because the log's only value is that a
    person can later ask why the agent watched someone."""
    account = await _account(session)
    await _flagged(session, account, "seven_times", 7)

    found = await _engine(session)._authors_over_threshold()

    assert found[0][2] == 7


async def test_the_per_cycle_limit_asks_a_human_instead(session):
    """Past the cap a human decides. The agent does not widen its own surveillance
    indefinitely on the strength of its own findings."""
    from src.models.agent_notification import AgentNotification

    account = await _account(session)
    for name in ("a_one", "b_two", "c_three"):
        await _flagged(session, account, name, 4)

    engine = _engine(session)
    engine.settings.auto_add_accounts_per_cycle = 1
    await engine.run_discovery()

    notifications = (
        await session.execute(select(AgentNotification))
    ).scalars().all()
    approvals = [n for n in notifications if n.notification_type == "DiscoveryApproval"]
    assert len(approvals) == 2, "the cap did not hand the rest to a human"

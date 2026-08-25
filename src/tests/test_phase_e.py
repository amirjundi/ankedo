"""Follow pacing and human handoff.

Both are about surviving contact with a real platform: how a replacement account
rebuilds coverage without burning itself, and what happens when a checkpoint appears
and nobody answers.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from src.core.settings import get_settings
from src.models.agent_worker_account import AccountStage, AccountState, AgentWorkerAccount
from src.models.follow_state import FollowState, FollowStatus
from src.models.tracked_account import AccountStatus, TrackedAccount


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("HANDOFF_TIMEOUT_MINUTES", "1")
    monkeypatch.setenv("HANDOFF_POLL_SECONDS", "1")
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


async def _worker(session, username="w1", stage=AccountStage.WARM_UP):
    worker = AgentWorkerAccount(
        platform="instagram", username=username,
        password_encrypted="x", fingerprint_seed="x",
        state=AccountState.HEALTHY, stage=stage,
    )
    session.add(worker)
    await session.commit()
    return worker


async def _targets(session, count=20, priority=0):
    made = []
    for i in range(count):
        target = TrackedAccount(
            platform="instagram", handle=f"t{i}",
            page_url=f"https://instagram.com/t{i}/",
            status=AccountStatus.ACTIVE, priority=priority,
        )
        session.add(target)
        made.append(target)
    await session.commit()
    return made


def _manager(session, seed=0):
    from src.browsers.follow_manager import FollowManager

    return FollowManager(session, rng=random.Random(seed))


# ------------------------------------------------------------ follow pacing


async def test_backlog_covers_every_active_target(session):
    worker = await _worker(session)
    await _targets(session, 5)

    assert await _manager(session).plan_for_account(worker) == 5


async def test_planning_twice_does_not_duplicate(session):
    worker = await _worker(session)
    await _targets(session, 5)
    manager = _manager(session)

    await manager.plan_for_account(worker)
    assert await manager.plan_for_account(worker) == 0


async def test_warmup_quota_caps_the_batch(session):
    """200 follows in an hour is an instant flag — warm-up exists to prevent that."""
    worker = await _worker(session, stage=AccountStage.WARM_UP)
    await _targets(session, 50)
    manager = _manager(session)
    await manager.plan_for_account(worker)

    batch = await manager.next_batch(worker)
    assert len(batch) == get_settings().warmup_follows_per_day
    assert len(batch) < 50


async def test_established_accounts_get_more_headroom(session):
    warm = await _worker(session, "warm", stage=AccountStage.WARM_UP)
    active = await _worker(session, "active", stage=AccountStage.ACTIVE)
    await _targets(session, 50)
    manager = _manager(session)
    await manager.plan_for_account(warm)
    await manager.plan_for_account(active)

    assert len(await manager.next_batch(active)) > len(await manager.next_batch(warm))


async def test_quota_counts_todays_follows(session):
    worker = await _worker(session, stage=AccountStage.WARM_UP)
    await _targets(session, 50)
    manager = _manager(session)
    await manager.plan_for_account(worker)

    quota = get_settings().warmup_follows_per_day
    for follow in (await manager.next_batch(worker))[:quota]:
        await manager.record(follow, FollowStatus.FOLLOWING)

    assert await manager.next_batch(worker) == [], "must stop once the day's quota is spent"


async def test_priority_targets_come_first(session):
    worker = await _worker(session)
    await _targets(session, 5, priority=0)
    await _targets(session, 3, priority=10)
    manager = _manager(session)
    await manager.plan_for_account(worker)

    batch = await manager.next_batch(worker)
    assert all(f.priority == 10 for f in batch[:3]), "high priority first"


async def test_order_is_shuffled_within_a_priority_band(session):
    """Replaying the banned account's order fingerprints the replacement."""
    worker = await _worker(session)
    await _targets(session, 20)
    await _manager(session).plan_for_account(worker)

    first = [f.tracked_account_id for f in await _manager(session, seed=1).next_batch(worker)]
    second = [f.tracked_account_id for f in await _manager(session, seed=99).next_batch(worker)]
    assert first != second


# ------------------------------------------------------------- ban recovery


async def test_replacement_inherits_coverage_not_timing(session):
    banned = await _worker(session, "old", stage=AccountStage.ACTIVE)
    replacement = await _worker(session, "new", stage=AccountStage.WARM_UP)
    targets = await _targets(session, 6)
    manager = _manager(session)

    await manager.plan_for_account(banned)
    for follow in await manager.next_batch(banned):
        await manager.record(follow, FollowStatus.FOLLOWING)

    inherited = await manager.inherit_from(banned, replacement)

    assert inherited > 0
    from sqlalchemy import select

    rows = (
        await session.execute(
            select(FollowState).where(FollowState.worker_account_id == replacement.id)
        )
    ).scalars().all()
    assert all(r.status == FollowStatus.PENDING for r in rows), (
        "inherited coverage must be re-followed at the replacement's own pace"
    )
    assert all(r.followed_at is None for r in rows)


async def test_only_real_coverage_is_inherited(session):
    """Targets the banned account never actually followed are not coverage."""
    banned = await _worker(session, "old")
    replacement = await _worker(session, "new")
    await _targets(session, 10)
    manager = _manager(session)
    await manager.plan_for_account(banned)

    batch = await manager.next_batch(banned)
    await manager.record(batch[0], FollowStatus.FOLLOWING)
    await manager.record(batch[1], FollowStatus.FAILED, "target blocked us")

    assert await manager.inherit_from(banned, replacement) == 1


async def test_coverage_gaps_are_visible(session):
    """Targets nobody follows degrade monitoring silently without this."""
    worker = await _worker(session)
    await _targets(session, 5)
    manager = _manager(session)
    await manager.plan_for_account(worker)

    assert len(await manager.coverage_gaps("instagram")) == 5

    batch = await manager.next_batch(worker)
    await manager.record(batch[0], FollowStatus.FOLLOWING)
    assert len(await manager.coverage_gaps("instagram")) == 4


# ---------------------------------------------------------------- handoff


@pytest.fixture
def instant_polls(monkeypatch):
    """Run the wait loop without real delay.

    The loop counts elapsed time from its own sleeps, so stubbing sleep makes the
    timeout arrive immediately while still exercising every poll iteration.
    """
    async def no_sleep(_):
        return None

    monkeypatch.setattr("src.browsers.handoff.asyncio.sleep", no_sleep)


class FakePage:
    def __init__(self, blocked=True):
        self.url = "https://facebook.com/checkpoint/12345"
        self._blocked = blocked
        self.shots = 0

    async def screenshot(self, path=None):
        self.shots += 1

    async def content(self):
        return "<html>checkpoint</html>" if self._blocked else "<html>feed</html>"

    def clear(self):
        self._blocked = False
        self.url = "https://facebook.com/feed"


async def test_unanswered_handoff_quarantines_the_account(session, instant_polls):
    """Repeatedly hitting a checkpoint turns a challenge into a permanent ban."""
    from src.browsers.handoff import HumanHandoff

    worker = await _worker(session, "fb1", stage=AccountStage.ACTIVE)
    page = FakePage(blocked=True)

    resolved = await HumanHandoff(session, page, worker).request("captcha", kind="captcha")

    assert resolved is False
    assert worker.state == AccountState.BLOCKED
    assert worker.stage == AccountStage.QUARANTINE


async def test_resolved_handoff_leaves_the_account_healthy(session, instant_polls):
    from src.browsers.handoff import HumanHandoff

    worker = await _worker(session, "fb2", stage=AccountStage.ACTIVE)
    page = FakePage(blocked=True)
    page.clear()  # human solved it before the first poll

    assert await HumanHandoff(session, page, worker).request("captcha") is True
    assert worker.state == AccountState.HEALTHY


async def test_handoff_alerts_the_admin_with_a_screenshot(session, instant_polls):
    from sqlalchemy import select

    from src.browsers.handoff import HumanHandoff
    from src.models.agent_notification import AgentNotification

    worker = await _worker(session, "fb3")
    page = FakePage(blocked=True)
    await HumanHandoff(session, page, worker).request("captcha")

    notifications = (await session.execute(select(AgentNotification))).scalars().all()
    kinds = [n.notification_type for n in notifications]
    assert "HumanInterventionRequired" in kinds
    assert "AccountQuarantined" in kinds
    assert page.shots == 1

    alert = next(n for n in notifications if n.notification_type == "HumanInterventionRequired")
    assert alert.urgency == "Critical"
    assert alert.context_data["screenshot"], "the admin needs to see what blocked it"

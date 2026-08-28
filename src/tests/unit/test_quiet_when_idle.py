"""A fresh install must not alert about itself.

CapacityAlert fired for every platform below the minimum healthy accounts — and zero
is below the minimum. So an agent installed five minutes ago raised three High alerts
a minute, forever, about platforms nobody had configured yet. The operator watched
forty-five of them accumulate and time out in a single log.

Worse than noise. An alert that fires on a healthy new install teaches whoever reads
it that these alerts mean nothing, so the one that matters — a platform whose accounts
were all banned overnight — arrives looking exactly like the forty-five they have
already learned to scroll past.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

import src.core.database  # noqa: F401
from src.core.settings import get_settings
from src.models.agent_notification import AgentNotification
from src.models.agent_worker_account import AccountState, AgentWorkerAccount


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'q.db'}")
    monkeypatch.setenv("ETTOK_BASE_URL", "")
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


def _loop(session):
    from src.core.orchestration_loop import OrchestrationLoop

    return OrchestrationLoop(session)


async def _alerts(session):
    rows = (await session.execute(select(AgentNotification))).scalars().all()
    return [n for n in rows if n.notification_type == "CapacityAlert"]


async def test_a_fresh_install_raises_no_alerts(session):
    await _loop(session)._check_alerts()

    assert await _alerts(session) == []


async def test_repeated_cycles_stay_quiet(session):
    """The operator's actual symptom: three a minute, forever."""
    loop = _loop(session)
    for _ in range(5):
        await loop._check_alerts()

    assert await _alerts(session) == []


async def test_a_platform_whose_accounts_all_broke_still_alerts(session):
    """The alert this exists for. Configured accounts, none of them healthy."""
    for i in range(3):
        session.add(
            AgentWorkerAccount(
                platform="facebook", username=f"w{i}", state=AccountState.BLOCKED,
                password_encrypted="x", fingerprint_seed="s",
            )
        )
    await session.commit()

    await _loop(session)._check_alerts()
    alerts = await _alerts(session)

    assert len(alerts) == 1
    assert alerts[0].urgency == "High"


async def test_a_healthy_platform_does_not_alert(session):
    settings = get_settings()
    for i in range(settings.min_healthy_accounts_per_platform + 1):
        session.add(
            AgentWorkerAccount(
                platform="facebook", username=f"h{i}", state=AccountState.HEALTHY,
                password_encrypted="x", fingerprint_seed="s",
            )
        )
    await session.commit()

    await _loop(session)._check_alerts()

    assert await _alerts(session) == []


async def test_only_configured_platforms_are_considered(session):
    """One platform set up must not produce alerts about two others nobody uses."""
    session.add(
        AgentWorkerAccount(
            platform="facebook", username="w", state=AccountState.BLOCKED,
            password_encrypted="x", fingerprint_seed="s",
        )
    )
    await session.commit()

    await _loop(session)._check_alerts()
    alerts = await _alerts(session)

    assert len(alerts) == 1
    assert alerts[0].context_data["platform"] == "facebook"

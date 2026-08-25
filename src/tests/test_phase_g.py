"""Trend detection and bounded self-configuration.

The two properties that matter most here are negative ones: downtime must not read as
a spike, and the agent must not be able to change what counts as hate speech.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from src.core.settings import get_settings


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("TREND_MIN_HISTORY_HOURS", "5")
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


def _detector(session):
    from src.core.trend_detector import TrendDetector

    return TrendDetector(session)


async def _history(session, densities, *, group="yazidi", platform="facebook", scanned=100):
    """Seed hourly buckets ending at the current hour."""
    detector = _detector(session)
    now = datetime.now(timezone.utc)
    for offset, density in enumerate(reversed(densities)):
        when = now - timedelta(hours=offset)
        await detector.record(
            target_group=group,
            platform=platform,
            scanned=scanned,
            flagged=int(scanned * density),
            when=when,
        )


# ------------------------------------------------------------ trend detection


async def test_steady_traffic_is_not_a_spike(session):
    await _history(session, [0.05] * 10)
    assert await _detector(session).detect() == []


async def test_a_sudden_surge_is_detected(session):
    await _history(session, [0.02] * 10 + [0.40])
    spikes = await _detector(session).detect()

    assert len(spikes) == 1
    assert spikes[0].target_group == "yazidi"
    assert spikes[0].multiple > 5


async def test_downtime_never_reads_as_a_spike(session):
    """The host runs business hours sometimes; an idle night is not calm."""
    from src.core.trend_detector import TrendDetector, hour_bucket

    await _history(session, [0.02] * 10)
    now = datetime.now(timezone.utc)
    unobserved = [hour_bucket(now - timedelta(hours=h)) for h in range(20, 30)]
    await TrendDetector(session).mark_unobserved(unobserved, "yazidi", "facebook")

    # A normal hour after the gap must not look anomalous.
    assert await _detector(session).detect() == []


async def test_tiny_samples_do_not_trigger_escalation(session):
    """Two flagged comments out of three is a wild rate, not an incident."""
    await _history(session, [0.02] * 10)
    await _detector(session).record(
        target_group="yazidi", platform="facebook", scanned=3, flagged=2
    )

    assert await _detector(session).detect() == []


async def test_insufficient_history_yields_no_claim(session):
    await _history(session, [0.02, 0.9])
    assert await _detector(session).detect() == []


async def test_spikes_are_scoped_per_group(session):
    """A surge against one community must not escalate crawling for another."""
    await _history(session, [0.02] * 10 + [0.50], group="yazidi")
    await _history(session, [0.02] * 11, group="christian-iraqi")

    spikes = await _detector(session).detect()
    assert [s.target_group for s in spikes] == ["yazidi"]


async def test_spike_description_is_human_readable(session):
    """An NGO has to be able to explain why the system escalated."""
    await _history(session, [0.02] * 10 + [0.40])
    text = (await _detector(session).detect())[0].describe()

    assert "yazidi" in text and "baseline" in text


# ------------------------------------------------------- self-configuration


def _tuner(session):
    from src.core.self_tuner import SelfTuner

    return SelfTuner(session)


async def test_operational_parameters_are_self_tunable(session):
    applied = await _tuner(session).adjust(
        "pacing_min_delay_seconds", 6.0, "block rate rose on facebook"
    )
    assert applied == 6.0
    assert await _tuner(session).current("pacing_min_delay_seconds") == 6.0


async def test_values_are_clamped_to_human_set_bounds(session):
    """The guardrail is structural — the agent cannot exceed it by asking."""
    applied = await _tuner(session).adjust("pacing_min_delay_seconds", 9999, "panic")
    assert applied == 30.0


@pytest.mark.parametrize(
    "key", ["auto_flag_threshold", "borderline_low", "daily_token_budget", "trend_zscore_threshold"]
)
async def test_content_decisions_cannot_be_self_tuned(session, key):
    """An agent that can raise its own threshold can quietly stop detecting hate."""
    with pytest.raises(PermissionError, match="proposal-only"):
        await _tuner(session).adjust(key, 0.99, "would rather flag less")


async def test_a_restricted_change_becomes_a_proposal(session):
    from sqlalchemy import select

    from src.models.agent_notification import AgentNotification

    await _tuner(session).propose("auto_flag_threshold", 0.9, "precision is low")

    notification = (await session.execute(select(AgentNotification))).scalars().one()
    assert notification.notification_type == "ConfigChangeProposal"
    assert notification.context_data["proposed"] == 0.9


async def test_unknown_keys_are_rejected(session):
    with pytest.raises(KeyError):
        await _tuner(session).adjust("something_invented", 1, "why not")


async def test_changes_record_who_and_why(session):
    """"The agent changed something and we don't know what" is unrecoverable."""
    from src.models.agent_config import TunedBy

    await _tuner(session).adjust("max_posts_per_account", 25, "queue was starving")
    row = (await _tuner(session).history())[0]

    assert row.tuned_by == TunedBy.AGENT
    assert row.reason == "queue was starving"
    assert row.changed_at is not None


async def test_a_change_can_be_reverted(session):
    tuner = _tuner(session)
    await tuner.adjust("max_comments_per_post", 200, "wide sweep")
    assert await tuner.revert("max_comments_per_post") == 100.0


async def test_reverting_an_untouched_key_is_a_no_op(session):
    assert await _tuner(session).revert("max_posts_per_account") is None

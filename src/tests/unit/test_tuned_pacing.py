"""A tuned value must reach the code that uses it.

SelfTuner.adjust wrote an AgentConfig row that only SelfTuner.current read back,
while CamoufoxWorker.pacing_delay read settings.* — the static env value. The one
live autonomy feature in the system therefore tuned a number nothing consulted: a
detected spike raised an alert, wrote a row, and changed the crawl rate not at all.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from src.browsers.camoufox_worker import CamoufoxWorker
from src.core.settings import get_settings


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'tune.db'}")
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


def _worker():
    return CamoufoxWorker(platform="facebook", account_id="test", proxy=None)


def test_pacing_defaults_to_the_configured_values():
    settings = get_settings()
    assert _worker().pacing_bounds == (
        settings.pacing_min_delay_seconds,
        settings.pacing_max_delay_seconds,
    )


def test_set_pacing_overrides_the_configured_values():
    worker = _worker()
    worker.set_pacing(0.8, 3.0)

    assert worker.pacing_bounds == (0.8, 3.0)


def test_an_inverted_range_cannot_be_applied():
    """A max below the min would make the Gaussian nonsense and the clamp inverted."""
    worker = _worker()
    worker.set_pacing(5.0, 2.0)

    minimum, maximum = worker.pacing_bounds
    assert minimum <= maximum


async def test_a_tuned_value_reaches_the_worker(session):
    """The join that was missing: tuner writes, collector reads, worker paces."""
    from src.core.self_tuner import SelfTuner

    tuner = SelfTuner(session)
    await tuner.adjust("pacing_min_delay_seconds", 1.2, "test")
    await tuner.adjust("pacing_max_delay_seconds", 3.4, "test")

    worker = _worker()
    worker.set_pacing(
        await tuner.current("pacing_min_delay_seconds"),
        await tuner.current("pacing_max_delay_seconds"),
    )

    assert worker.pacing_bounds == (1.2, 3.4)


async def test_a_spike_shortens_both_ends_of_the_range(session):
    """Shrinking only the floor barely moves the mean of the distribution."""
    from src.core.self_tuner import SelfTuner

    tuner = SelfTuner(session)
    settings = get_settings()
    multiplier = settings.crawl_multiplier_on_spike

    before_min = await tuner.current("pacing_min_delay_seconds")
    before_max = await tuner.current("pacing_max_delay_seconds")

    for key, floor in (("pacing_min_delay_seconds", 1.0), ("pacing_max_delay_seconds", 3.0)):
        current = await tuner.current(key)
        await tuner.adjust(key, max(floor, current / multiplier), "spike")

    after_min = await tuner.current("pacing_min_delay_seconds")
    after_max = await tuner.current("pacing_max_delay_seconds")

    assert after_min < before_min
    assert after_max < before_max, "the ceiling never moved, so the crawl rate barely changed"

    # The thing that actually matters: the mean delay drops meaningfully.
    assert (after_min + after_max) / 2 < (before_min + before_max) / 2 * 0.8

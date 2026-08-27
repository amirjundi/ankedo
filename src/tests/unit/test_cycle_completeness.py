"""Every step the cycle is supposed to perform is actually performed.

This whole class of fault has now happened four times in this codebase: a component
was written, tested in isolation, and never called. `PostProcessor` had no callers, so
nothing moved an item from Discovery to Classification. `build_verdict`, `drain` and
`submit_verdicts` had no callers, so classification stopped at the local database.
`LearningLoopWorker` was never instantiated, so review could not influence anything.
`requeue_inflight_on_restart` existed from the start and nothing called it.

Unit tests could not catch any of them, because each part worked perfectly alone. The
missing thing was always the wiring, so this file tests the wiring — it asserts that
one `run_cycle()` reaches each step, and nothing else.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from src.core.settings import get_settings


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'cycle.db'}")
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


EXPECTED_STEPS = [
    "_sync_workers",
    "_schedule_cases",
    "_collect",
    "_process_discovery",
    "_handle_notifications",
    "_process_queues",
    "_evaluate_expansion",
    "_prevent_reviewer_overload",
    "_check_alerts",
    "_check_trends",
    "_queue_scan_log",
    "_run_learning",
    "_drain_outbox",
    "_check_liveness",
]


@pytest.fixture
def traced_loop(session, monkeypatch):
    """A loop whose every step records that it ran, and does nothing else."""
    from src.core.orchestration_loop import OrchestrationLoop

    loop = OrchestrationLoop(session)
    ran: list[str] = []

    for name in EXPECTED_STEPS:
        assert hasattr(loop, name), f"the cycle has no step named {name}"

        def record(step=name):
            async def _run(*args, **kwargs):
                ran.append(step)

            return _run

        monkeypatch.setattr(loop, name, record())

    async def no_discovery():
        ran.append("run_discovery")

    monkeypatch.setattr(loop.discovery, "run_discovery", no_discovery)
    return loop, ran


async def test_one_cycle_reaches_every_step(traced_loop):
    loop, ran = traced_loop
    await loop.run_cycle()

    missing = [s for s in EXPECTED_STEPS if s not in ran]
    assert not missing, f"the cycle never calls: {missing}"


async def test_verdicts_are_shipped_after_they_are_produced(traced_loop):
    """Draining before classification would post this cycle's work next cycle, and
    an agent that is stopped between the two would never post it at all."""
    loop, ran = traced_loop
    await loop.run_cycle()

    assert ran.index("_process_queues") < ran.index("_drain_outbox")


async def test_the_scan_log_is_recorded_before_it_is_sent(traced_loop):
    loop, ran = traced_loop
    await loop.run_cycle()

    assert ran.index("_queue_scan_log") < ran.index("_drain_outbox")


async def test_collection_happens_before_the_queue_is_processed(traced_loop):
    """The original gap: discovery filled a queue that nothing drained into
    classification."""
    loop, ran = traced_loop
    await loop.run_cycle()

    assert ran.index("_collect") < ran.index("_process_discovery")
    assert ran.index("_process_discovery") < ran.index("_process_queues")


async def test_liveness_is_checked_last(traced_loop):
    """The dead man's switch judges the cycle, so it has to run after it."""
    loop, ran = traced_loop
    await loop.run_cycle()

    assert ran[-1] == "_check_liveness"

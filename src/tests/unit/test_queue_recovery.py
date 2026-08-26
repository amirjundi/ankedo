"""Work must survive a failed attempt.

dequeue claims an item by setting is_inflight, and only ever selects rows where it
is false. Nothing released the claim when classification failed, so an item that
failed once became permanently invisible — never retried, never reported, gone. On
an endpoint that comes and goes, which is the deployment case, that silently
discarded every item attempted while it was down. The agent looked like it was
working through a queue; the queue was quietly emptying into nothing.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.core.queue_manager import QueueManager
from src.core.settings import get_settings
from src.models.post import Post, QueueState
from src.models.queue_item import QueueItem, QueueStage
from src.models.tracked_account import AccountSource, AccountStatus, TrackedAccount


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'q.db'}")
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


async def _queued(session, count=1, priority=0):
    account = TrackedAccount(platform="facebook", handle="p",
                             status=AccountStatus.WARMUP, source=AccountSource.MANUAL)
    session.add(account)
    await session.flush()

    items = []
    for i in range(count):
        post = Post(tracked_account_id=account.id, platform="facebook",
                    platform_post_id=f"p{i}", url=f"https://x/{i}",
                    content_text="نص", queue_state=QueueState.CLASSIFICATION)
        session.add(post)
        await session.flush()
        item = QueueItem(stage=QueueStage.CLASSIFICATION, post_id=post.id,
                         tracked_account_id=account.id, is_inflight=False,
                         priority=priority)
        session.add(item)
        items.append(item)
    await session.commit()
    return items


async def test_a_released_item_is_dequeued_again(session):
    """The whole bug: without release it is claimed forever and never retried."""
    queue = QueueManager(session)
    await _queued(session)

    first = await queue.dequeue(QueueStage.CLASSIFICATION, worker_id="w1")
    assert first is not None

    # While it is claimed, nothing else can see it.
    assert await queue.dequeue(QueueStage.CLASSIFICATION, worker_id="w2") is None

    await queue.release(first, "model endpoint unavailable")

    again = await queue.dequeue(QueueStage.CLASSIFICATION, worker_id="w2")
    assert again is not None and again.id == first.id


async def test_release_clears_the_lock_fields(session):
    queue = QueueManager(session)
    await _queued(session)

    item = await queue.dequeue(QueueStage.CLASSIFICATION, worker_id="w1")
    await queue.release(item, "failed")

    await session.refresh(item)
    assert item.is_inflight is False
    assert item.locked_by_worker is None
    assert item.locked_at is None


async def test_a_repeatedly_failing_item_goes_behind_the_others(session):
    """A poison item that fails every cycle would otherwise be picked first every
    time and starve everything behind it."""
    queue = QueueManager(session)
    await _queued(session, count=2)

    poison = await queue.dequeue(QueueStage.CLASSIFICATION, worker_id="w1")
    await queue.release(poison, "always fails")

    # The other item now outranks it.
    following = await queue.dequeue(QueueStage.CLASSIFICATION, worker_id="w2")
    assert following.id != poison.id


async def test_recovery_returns_items_stranded_by_a_dead_process(session):
    """A kill mid-classification leaves the claim set with no one holding it."""
    queue = QueueManager(session)
    await _queued(session, count=3)

    for worker in ("w1", "w2", "w3"):
        await queue.dequeue(QueueStage.CLASSIFICATION, worker_id=worker)
    assert await queue.dequeue(QueueStage.CLASSIFICATION, worker_id="w4") is None

    recovered = await queue.requeue_inflight_on_restart()

    assert recovered == 3
    assert await queue.dequeue(QueueStage.CLASSIFICATION, worker_id="w4") is not None


async def test_the_loop_recovers_stranded_items_before_its_first_cycle(session):
    """QueueManager has had this recovery from the start and nothing called it."""
    from src.core.orchestration_loop import OrchestrationLoop

    queue = QueueManager(session)
    await _queued(session, count=2)
    for worker in ("w1", "w2"):
        await queue.dequeue(QueueStage.CLASSIFICATION, worker_id=worker)

    loop = OrchestrationLoop(session)

    # run_forever never returns; run only the recovery it performs on entry.
    recovered = await loop.queue_manager.requeue_inflight_on_restart()
    assert recovered == 2

    stuck = (
        await session.execute(
            select(QueueItem).where(QueueItem.is_inflight.is_(True))
        )
    ).scalars().all()
    assert stuck == []


async def test_a_failing_classification_does_not_lose_the_item(session, monkeypatch):
    """End to end through the loop's own failure path."""
    from src.core.orchestration_loop import OrchestrationLoop

    await _queued(session)
    loop = OrchestrationLoop(session)

    async def always_fails(self, item):
        raise RuntimeError("Connection error — endpoint down")

    monkeypatch.setattr(
        "src.classifiers.classification_worker.ClassificationWorker.process_item",
        always_fails,
    )

    await loop._process_queues()

    survivor = (await session.execute(select(QueueItem))).scalar_one()
    assert survivor.is_inflight is False, "the item was stranded by a failed attempt"
    assert survivor.stage == QueueStage.CLASSIFICATION, "it is still waiting to be done"

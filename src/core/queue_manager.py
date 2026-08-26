"""
AsyncIO multi-stage queue manager with SQLite persistence.
Stages: Discovery -> Processing -> Classification -> Review
"""
from __future__ import annotations

from typing import Sequence

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.post import Post, QueueState as PostQueueState
from src.models.queue_item import QueueItem, QueueStage
from src.core.settings import get_settings

log = structlog.get_logger()


class QueueManager:
    """Manages the flow of items between queue stages."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def enqueue(
        self,
        tracked_account_id: str,
        post_id: str,
        case_id: str | None = None,
        priority: int = 0,
        stage: QueueStage = QueueStage.DISCOVERY,
    ) -> QueueItem:
        """Put a post into the pipeline at a given stage.

        Discovery is the default because that is where a crawled post starts: it has
        been seen, but its comments have not been fetched yet. Content that arrives
        with its comments already attached — a capture from the browser extension —
        has nothing for the Processing stage to do and enters at Classification
        instead. Sending it to Discovery would park it behind a browser fetch that is
        neither needed nor possible.
        """
        item = QueueItem(
            stage=stage,
            priority=priority,
            case_id=case_id,
            tracked_account_id=tracked_account_id,
            post_id=post_id,
            is_inflight=False,
        )
        self.session.add(item)
        await self.session.commit()
        log.info("Item enqueued", post_id=post_id, stage=stage.value)
        return item

    async def enqueue_discovery(self, tracked_account_id: str, post_id: str, case_id: str | None = None, priority: int = 0) -> QueueItem:
        """Enqueue a newly discovered post, whose comments still need fetching."""
        return await self.enqueue(
            tracked_account_id, post_id, case_id, priority, QueueStage.DISCOVERY
        )

    async def dequeue(self, stage: QueueStage, worker_id: str) -> QueueItem | None:
        """
        Dequeue an item for a specific stage, respecting priority.
        High priority items (incident cases) are dequeued before watch-list items.
        Returns the item locked for the given worker.
        """
        # Apply backpressure: if discovery is requested, check classification queue depth
        if stage == QueueStage.DISCOVERY:
            stmt_count = select(QueueItem).where(QueueItem.stage == QueueStage.CLASSIFICATION)
            result_count = await self.session.execute(stmt_count)
            class_count = len(result_count.scalars().all())
            if class_count >= self.settings.classification_queue_high_water:
                log.warning("Backpressure applied: Classification queue full", class_count=class_count)
                return None

        # Priority dequeue logic
        stmt = (
            select(QueueItem)
            .where(QueueItem.stage == stage)
            .where(QueueItem.is_inflight == False)  # noqa: E712
            .order_by(QueueItem.priority.desc(), QueueItem.created_at.asc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        item = result.scalar_one_or_none()
        if item is None:
            return None

        # Claim it with a CONDITIONAL update, not a plain assignment.
        #
        # SELECT-then-UPDATE is a race: two workers both read the row while
        # is_inflight is false, both set it true, and the same post is classified
        # twice — double the LLM spend and two reports about one person. Putting
        # `is_inflight == False` in the WHERE clause makes the database arbitrate:
        # exactly one UPDATE matches a row, and the loser sees rowcount 0.
        from datetime import datetime, timezone

        claim = (
            update(QueueItem)
            .where(QueueItem.id == item.id, QueueItem.is_inflight.is_(False))
            .values(
                is_inflight=True,
                locked_by_worker=worker_id,
                locked_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        claimed = await self.session.execute(claim)
        await self.session.commit()

        if claimed.rowcount == 0:
            # Another worker won it between our select and our update. Not an error —
            # the caller simply asks again.
            log.debug("Lost claim race", item_id=item.id, worker_id=worker_id)
            return None

        await self.session.refresh(item)
        log.debug("Dequeued item", item_id=item.id, stage=stage.value, worker_id=worker_id)
        return item

    async def promote(self, item: QueueItem, next_stage: QueueStage) -> None:
        """Promote an item to the next stage."""
        item.stage = next_stage
        item.is_inflight = False
        item.locked_by_worker = None
        item.locked_at = None
        
        # If this is tied to a Post, update the post's queue_state as well
        if item.post_id:
            stmt = select(Post).where(Post.id == item.post_id)
            result = await self.session.execute(stmt)
            post = result.scalar_one_or_none()
            if post:
                # Map QueueStage to PostQueueState
                try:
                    post.queue_state = PostQueueState(next_stage.value)
                    self.session.add(post)
                except ValueError:
                    pass # Done/Rejected mapping handled in mark_done
                    
        self.session.add(item)
        await self.session.commit()
        log.info("Item promoted", item_id=item.id, next_stage=next_stage.value)

    async def mark_done(self, item: QueueItem, final_state: PostQueueState = PostQueueState.DONE) -> None:
        """Mark an item as complete and remove from the active queue."""
        if item.post_id:
            stmt = select(Post).where(Post.id == item.post_id)
            result = await self.session.execute(stmt)
            post = result.scalar_one_or_none()
            if post:
                post.queue_state = final_state
                self.session.add(post)
        
        await self.session.delete(item)
        await self.session.commit()
        log.info("Item marked done", item_id=item.id, final_state=final_state.value)

    async def release(self, item: QueueItem, reason: str) -> None:
        """Return a claimed item to the queue after a failed attempt.

        dequeue claims an item by setting is_inflight, and dequeue only ever selects
        rows where it is false. Nothing released the claim on failure, so an item that
        failed once became permanently invisible: never retried, never reported, just
        gone. On an endpoint that comes and goes — which is the deployment case — that
        silently discarded every item attempted while it was down.

        The item goes to the back of the queue rather than straight back to the front.
        A poison item that fails every time would otherwise be picked first on every
        cycle and starve everything behind it.
        """
        item.is_inflight = False
        item.locked_by_worker = None
        item.locked_at = None
        item.priority = min(item.priority - 1, -1)
        await self.session.commit()
        log.info("Item returned to the queue", item_id=item.id, reason=reason[:200])

    async def requeue_inflight_on_restart(self) -> int:
        """Crash recovery: Find inflight items on startup and return them to the queue."""
        stmt = (
            update(QueueItem)
            .where(QueueItem.is_inflight == True)  # noqa: E712
            .values(is_inflight=False, locked_by_worker=None, locked_at=None)
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        requeued = result.rowcount
        if requeued > 0:
            log.info("Crash recovery: Requeued stuck items", count=requeued)
        return requeued

    async def record_post_statistics(self, post_id: str, comments_total: int, comments_flagged: int) -> None:
        """Update per-post statistics atomically after classification completes."""
        stmt = select(Post).where(Post.id == post_id)
        result = await self.session.execute(stmt)
        post = result.scalar_one_or_none()
        if post:
            post.comments_total = comments_total
            post.comments_flagged = comments_flagged
            self.session.add(post)
            await self.session.commit()
            log.info("Recorded post statistics", post_id=post_id, total=comments_total, flagged=comments_flagged)

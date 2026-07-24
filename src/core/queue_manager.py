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

    async def enqueue_discovery(self, tracked_account_id: str, post_id: str, case_id: str | None = None, priority: int = 0) -> QueueItem:
        """Enqueue a new post for processing (starts at Discovery stage)."""
        item = QueueItem(
            stage=QueueStage.DISCOVERY,
            priority=priority,
            case_id=case_id,
            tracked_account_id=tracked_account_id,
            post_id=post_id,
            is_inflight=False,
        )
        self.session.add(item)
        await self.session.commit()
        log.info("Item enqueued for discovery", post_id=post_id, stage=QueueStage.DISCOVERY.value)
        return item

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

        if item:
            item.is_inflight = True
            item.locked_by_worker = worker_id
            from datetime import datetime, timezone
            item.locked_at = datetime.now(timezone.utc).isoformat()
            self.session.add(item)
            await self.session.commit()
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

"""
Collector Worker - Fetch new posts from tracked accounts and enqueue them.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.browsers.camoufox_worker import CamoufoxWorker
from src.core.queue_manager import QueueManager
from src.models.post import Post
from src.models.tracked_account import TrackedAccount
from src.platforms.base_adapter import PlatformAdapter

log = structlog.get_logger()


class CollectorWorker:
    """Async worker that discovers new posts from a tracked account."""

    def __init__(self, session: AsyncSession, queue_manager: QueueManager, browser_worker: CamoufoxWorker, adapter: PlatformAdapter):
        self.session = session
        self.queue_manager = queue_manager
        self.browser_worker = browser_worker
        self.adapter = adapter

    async def collect_posts(self, account: TrackedAccount) -> int:
        """Fetch new posts and enqueue them."""
        log.info("Starting collection", account_id=account.id, handle=account.handle)
        
        if not account.page_url:
            log.error("Account has no page_url", account_id=account.id)
            return 0

        # Fetch posts via adapter
        new_posts_data = await self.adapter.fetch_new_posts(
            page=self.browser_worker.page, 
            account_url=account.page_url,
            max_posts=10
        )

        enqueued_count = 0
        for post_data in new_posts_data:
            platform_post_id = post_data["platform_post_id"]
            
            # Deduplication check (T027)
            stmt = select(Post).where(
                Post.tracked_account_id == account.id,
                Post.platform_post_id == platform_post_id
            )
            result = await self.session.execute(stmt)
            if result.scalar_one_or_none():
                log.debug("Post already exists, skipping", platform_post_id=platform_post_id)
                continue

            # Create new Post record
            post = Post(
                tracked_account_id=account.id,
                case_id=account.linked_case_id,
                platform=account.platform,
                platform_post_id=platform_post_id,
                url=post_data["url"],
                content_text=post_data.get("content_text"),
                content_media_urls=post_data.get("media_urls", []),
                author_name=post_data.get("author_name"),
                author_profile_picture=post_data.get("author_profile_picture"),
                collected_at=datetime.now(timezone.utc).isoformat()
            )
            self.session.add(post)
            await self.session.flush() # Flush to get post.id

            # Enqueue to Discovery queue
            priority = 10 if account.linked_case_id else 0
            await self.queue_manager.enqueue_discovery(
                tracked_account_id=account.id, 
                post_id=post.id, 
                case_id=account.linked_case_id,
                priority=priority
            )
            enqueued_count += 1

        # Update account last_crawled_at
        account.last_crawled_at = datetime.now(timezone.utc).isoformat()
        self.session.add(account)
        await self.session.commit()

        log.info("Collection finished", account_id=account.id, enqueued_count=enqueued_count)
        return enqueued_count

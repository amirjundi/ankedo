"""
Post Processor Worker - Fetch comments and OCR text for a discovered post.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.browsers.camoufox_worker import CamoufoxWorker
from src.core.queue_manager import QueueManager
from src.models.comment import Comment
from src.models.post import Post
from src.models.queue_item import QueueItem, QueueStage
from src.platforms.base_adapter import PlatformAdapter

log = structlog.get_logger()


class PostProcessor:
    """Async worker that processes posts (fetches comments, performs OCR) and promotes to Classification."""

    def __init__(self, session: AsyncSession, queue_manager: QueueManager, browser_worker: CamoufoxWorker, adapter: PlatformAdapter):
        self.session = session
        self.queue_manager = queue_manager
        self.browser_worker = browser_worker
        self.adapter = adapter

    async def process_item(self, queue_item: QueueItem) -> bool:
        """Process a QueueItem currently in Processing stage."""
        if queue_item.stage != QueueStage.PROCESSING or not queue_item.post_id:
            return False
            
        log.info("Starting post processing", post_id=queue_item.post_id)

        # Get Post
        stmt = select(Post).where(Post.id == queue_item.post_id)
        result = await self.session.execute(stmt)
        post = result.scalar_one_or_none()
        if not post:
            log.error("Post not found for processing", post_id=queue_item.post_id)
            await self.queue_manager.mark_done(queue_item) # Or error state
            return False

        # T028 OCR Stub
        if not post.content_text and post.content_media_urls:
            log.info("Image-only post detected, running OCR stub", post_id=post.id)
            post.is_image_only = True
            # In a real implementation, we would download the image and run Tesseract/easyocr
            post.ocr_text = "Stub OCR extracted text"
            self.session.add(post)

        # Fetch comments
        comments_data = await self.adapter.fetch_comments(
            page=self.browser_worker.page, 
            post_url=post.url,
            max_comments=100
        )

        for comment_data in comments_data:
            platform_comment_id = comment_data["platform_comment_id"]
            
            # Deduplication
            stmt_cmt = select(Comment).where(
                Comment.post_id == post.id,
                Comment.platform_comment_id == platform_comment_id
            )
            result_cmt = await self.session.execute(stmt_cmt)
            if result_cmt.scalar_one_or_none():
                continue

            comment = Comment(
                post_id=post.id,
                platform_comment_id=platform_comment_id,
                text=comment_data.get("text"),
                author_name=comment_data.get("author_name"),
                author_profile_picture=comment_data.get("author_profile_picture")
            )
            self.session.add(comment)

        await self.session.commit()

        # Promote to Classification queue
        await self.queue_manager.promote(queue_item, QueueStage.CLASSIFICATION)
        log.info("Post processing finished, promoted to Classification", post_id=post.id)
        return True

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

    async def _analyze_media(self, post: Post) -> None:
        """Download and classify the post's images.

        Failures here are logged and recorded, never fatal: losing the image is
        recoverable, losing the post is not. `ocr_text` still carries the text the
        model read out of the image, so the text pipeline keeps working unchanged.
        """
        from src.classifiers.group_resolver import GroupResolver
        from src.classifiers.media_analyzer import MediaAnalyzer

        images: list[bytes] = []
        for url in (post.content_media_urls or [])[:3]:
            data = await self._download(url)
            if data:
                images.append(data)

        if not images:
            post.ocr_failed = True
            log.warning("No media could be downloaded", post_id=post.id)
            return

        groups = await GroupResolver(self.session).resolve_all(post.content_text or "")
        analysis = await MediaAnalyzer(self.session).analyze(
            images,
            parent_post_text=post.content_text or "",
            target_groups=groups,
            case_id=post.case_id,
            post_id=post.id,
        )

        post.media_classification = analysis
        if analysis and not analysis.get("analysis_failed"):
            # The model transcribes what it reads, which replaces the OCR step.
            post.ocr_text = analysis.get("visible_text")
        else:
            post.ocr_failed = True

    async def _download(self, url: str) -> bytes | None:
        """Fetch media through the browser session.

        Deliberately not a bare HTTP client: these URLs are usually signed and
        session-scoped, and a request from a different context both fails and looks
        like scraping from an unrelated source.
        """
        try:
            response = await self.browser_worker.page.request.get(url)
            if response.ok:
                return await response.body()
            log.warning("Media fetch rejected", url=url, status=response.status)
        except Exception as exc:
            log.warning("Media fetch failed", url=url, error=str(exc))
        return None

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

        # Media is classified as imagery, not merely transcribed. A meme's payload is
        # usually the picture — OCR would return a caption that reads as innocuous.
        if post.content_media_urls:
            post.is_image_only = not post.content_text
            await self._analyze_media(post)
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

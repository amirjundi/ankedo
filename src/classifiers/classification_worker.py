"""
Classification Worker - Processes items from the Classification Queue.
Runs normalization, lexicon, trope engine, and committee orchestrator.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.committee.orchestrator import CommitteeOrchestrator
from src.classifiers.lexicon import LexiconMatcher
from src.classifiers.normalizer import Normalizer
from src.classifiers.trope_engine import TropeEngine
from src.core.queue_manager import QueueManager
from src.models.comment import Comment
from src.models.post import Post, QueueState as PostQueueState
from src.models.queue_item import QueueItem, QueueStage
from src.core.settings import get_settings

log = structlog.get_logger()


class ClassificationWorker:
    """Async worker that classifies posts and comments."""

    def __init__(self, session: AsyncSession, queue_manager: QueueManager):
        self.session = session
        self.queue_manager = queue_manager
        self.settings = get_settings()
        
        # Initialize pipeline components
        self.normalizer = Normalizer()
        self.lexicon = LexiconMatcher(session)
        self.trope_engine = TropeEngine(session)
        self.orchestrator = CommitteeOrchestrator()

    async def _classify_text(self, text: str, context: dict) -> dict:
        """Run the full classification pipeline on a piece of text."""
        lexicon_hits = await self.lexicon.scan_text(text)
        tropes_fired = await self.trope_engine.evaluate(text, lexicon_hits)
        
        result = await self.orchestrator.run(
            text=text,
            context=context,
            lexicon_hits=lexicon_hits,
            tropes_fired=tropes_fired
        )
        return result

    async def process_item(self, queue_item: QueueItem) -> bool:
        """Process a QueueItem currently in Classification stage."""
        if queue_item.stage != QueueStage.CLASSIFICATION or not queue_item.post_id:
            return False
            
        log.info("Starting classification", post_id=queue_item.post_id)

        # Get Post
        stmt = select(Post).where(Post.id == queue_item.post_id)
        result = await self.session.execute(stmt)
        post = result.scalar_one_or_none()
        
        if not post:
            await self.queue_manager.mark_done(queue_item)
            return False

        # 1. Classify the post itself
        post_text = post.content_text or post.ocr_text or ""
        post_result = await self._classify_text(post_text, context={"type": "post"})
        
        post.classification_score = post_result["classification_score"]
        post.hate_speech_flag = post_result["hate_speech_flag"]
        post.multi_agent_trace = post_result["trace"]
        
        needs_review = False
        if post.classification_score >= self.settings.auto_flag_threshold:
            needs_review = True
        elif self.settings.borderline_low <= post.classification_score <= self.settings.borderline_high:
            needs_review = True # Route borderlines for review

        # 2. Classify all comments
        stmt_cmts = select(Comment).where(Comment.post_id == post.id)
        result_cmts = await self.session.execute(stmt_cmts)
        comments = result_cmts.scalars().all()
        
        flagged_comments = 0
        for comment in comments:
            comment_text = comment.text or ""
            cmt_result = await self._classify_text(
                comment_text, 
                context={"type": "comment", "parent_post_text": post_text}
            )
            
            comment.classification_score = cmt_result["classification_score"]
            comment.hate_speech_flag = cmt_result["hate_speech_flag"]
            comment.multi_agent_trace = cmt_result["trace"]
            comment.context_bundle_used = post_text # Snapshot context
            
            if comment.classification_score >= self.settings.auto_flag_threshold:
                flagged_comments += 1
                needs_review = True
                
        # 3. Update post statistics (T016)
        await self.queue_manager.record_post_statistics(
            post_id=post.id, 
            comments_total=len(comments), 
            comments_flagged=flagged_comments
        )

        # 4. Route based on results
        if needs_review or post_result["committee_disagreement"]:
            log.info("Post flagged for review", post_id=post.id)
            await self.queue_manager.promote(queue_item, QueueStage.REVIEW)
        else:
            log.info("Post clear, marking done", post_id=post.id)
            await self.queue_manager.mark_done(queue_item, final_state=PostQueueState.DONE)
            
        return True

"""Classification worker — drains the classification queue.

Classifies the post, then each of its comments **in the context of that post**. The
per-comment context is the whole point: a comment is judged against what it replies
to, not in isolation (SRS §4.4.0).

Also maintains the counts the platform needs for hate-density reporting
(AGENT_CONTRACT amendment §1): `comments_total` is the denominator, and it only
exists here — the platform never sees what was looked at and cleared, only what was
flagged.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.committee.orchestrator import CommitteeOrchestrator
from src.classifiers.context_bundle import build_bundle
from src.core.queue_manager import QueueManager
from src.core.settings import get_settings
from src.models.comment import Comment
from src.models.post import Post, QueueState as PostQueueState
from src.models.queue_item import QueueItem, QueueStage

log = structlog.get_logger()


class ClassificationWorker:
    """Async worker that classifies posts and their comments."""

    def __init__(self, session: AsyncSession, queue_manager: QueueManager):
        self.session = session
        self.queue_manager = queue_manager
        self.settings = get_settings()
        self._orchestrator: CommitteeOrchestrator | None = None

    @property
    def orchestrator(self) -> CommitteeOrchestrator:
        """Built on first use.

        Constructing it eagerly would require an LLM key just to instantiate the
        worker, which would stop the whole orchestration loop — case scheduling,
        notifications, capacity alerts — on a machine that has nothing queued to
        classify.
        """
        if self._orchestrator is None:
            self._orchestrator = CommitteeOrchestrator(self.session)
        return self._orchestrator

    async def process_item(self, queue_item: QueueItem) -> bool:
        if queue_item.stage != QueueStage.CLASSIFICATION or not queue_item.post_id:
            return False

        post = (
            await self.session.execute(select(Post).where(Post.id == queue_item.post_id))
        ).scalar_one_or_none()
        if post is None:
            await self.queue_manager.mark_done(queue_item)
            return False

        log.info("Classifying post", post_id=post.id)

        # --- the post itself ---------------------------------------------------
        post_bundle = await build_bundle(self.session, post)
        post_result = await self.orchestrator.run(post_bundle)

        # A meme's payload is the image; its caption is often chosen to read as
        # innocuous precisely so a text classifier clears it. The more severe of the
        # two verdicts wins.
        if post.media_classification:
            from src.classifiers.media_analyzer import merge_with_text

            post_result = merge_with_text(post_result, post.media_classification)

        self._apply(post, post_result)

        needs_review = self._needs_review(post_result)

        # --- each comment, judged against the post ----------------------------
        comments = (
            await self.session.execute(select(Comment).where(Comment.post_id == post.id))
        ).scalars().all()

        flagged = 0
        for comment in comments:
            if not (comment.text or "").strip():
                continue
            bundle = await build_bundle(self.session, post, comment)
            result = await self.orchestrator.run(bundle)

            comment.classification_score = result["classification_score"]
            comment.hate_speech_flag = result["hate_speech_flag"]
            comment.multi_agent_trace = result["trace"]
            # The bundle, not a bare string — what was judged has to be reconstructable.
            comment.context_bundle_used = bundle.to_dict()

            if result["hate_speech_flag"]:
                flagged += 1
            if self._needs_review(result):
                needs_review = True

        await self.queue_manager.record_post_statistics(
            post_id=post.id, comments_total=len(comments), comments_flagged=flagged
        )

        if needs_review:
            log.info("Routing to review", post_id=post.id, flagged=flagged)
            await self.queue_manager.promote(queue_item, QueueStage.REVIEW)
        else:
            await self.queue_manager.mark_done(queue_item, final_state=PostQueueState.DONE)

        await self.session.commit()
        return True

    def _needs_review(self, result: dict) -> bool:
        """Route to a human on confidence, ambiguity, or disagreement (FR-CL-11)."""
        score = result["classification_score"]
        if result["verdict"] == "ambiguous" or result["committee_disagreement"]:
            return True
        if result["hate_speech_flag"] and score >= self.settings.auto_flag_threshold:
            return True
        return self.settings.borderline_low <= score <= self.settings.borderline_high

    def _apply(self, post: Post, result: dict) -> None:
        post.classification_score = result["classification_score"]
        post.hate_speech_flag = result["hate_speech_flag"]
        post.multi_agent_trace = result["trace"]
        # FR-CL-14: record what produced the verdict, so it stays reproducible.
        post.classification_model_versions = {
            agent: (result["trace"].get(agent) or {}).get("model")
            for agent in ("triage", "specialist", "critic")
        }
        post.lexicon_version = self._version(result, "lexicon_hits")
        post.trope_version = self._version(result, "tropes_fired")

    @staticmethod
    def _version(result: dict, key: str) -> str | None:
        for entry in result["trace"].get(key) or []:
            if entry.get("pack_version"):
                return entry["pack_version"]
        return None

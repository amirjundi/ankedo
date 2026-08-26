"""
API Endpoints for Human-in-the-Loop Review.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import session_scope
from src.core.queue_manager import QueueManager
from src.models.case import Case
from src.models.post import Post, QueueState
from src.models.queue_item import QueueItem, QueueStage
from src.models.reviewer_decision import ReviewerDecision
from src.models.target_group import TargetGroup

log = structlog.get_logger()
router = APIRouter(prefix="/api/review", tags=["review"])


class ReviewSubmission(BaseModel):
    reviewer_id: str
    is_confirmed: bool
    rationale: str | None = None
    # For simplicity, we just review the post in this stub. In reality, comments would be specified too.


@router.get("/queue")
async def get_review_queue(session: AsyncSession = Depends(session_scope)):
    """Fetch items waiting in the review queue."""
    stmt = (
        select(QueueItem, Post)
        .join(Post, QueueItem.post_id == Post.id)
        .where(QueueItem.stage == QueueStage.REVIEW)
        .order_by(QueueItem.priority.desc())
        .limit(20)
    )
    result = await session.execute(stmt)
    items = []
    for q_item, post in result.all():
        # T044: parent-post-always-visible rule
        # Even if a specific comment was flagged, we send the post text
        # If the post text is missing, we flag a warning
        missing_context = not bool(post.content_text)
        
        trace = post.multi_agent_trace or {}

        items.append({
            "queue_item_id": q_item.id,
            "post_id": post.id,
            "platform": post.platform,
            "url": post.url,
            "content": post.content_text,
            "author": post.author_name,
            "missing_context_warning": missing_context,
            "score": post.classification_score if not missing_context else max(0.0, (post.classification_score or 0) - 0.2),
            "trace": trace,
            "tropes_fired": trace.get("tropes_fired") or [],
            "target_group": await _group_name(session, post),
            "case_title": await _case_title(session, post),
            # Prior confirmed verdicts against the same author. Shown to the reviewer
            # as evidence — deliberately NOT added to ContextBundle. A classifier told
            # that an author has been flagged before will flag them again, and the
            # corroboration it produces is its own. The human can weigh a pattern; the
            # model would only compound it.
            "prior_confirmed": await _prior_confirmed(session, post),
        })
    return {"queue": items}


async def _group_name(session: AsyncSession, post: Post) -> str | None:
    if not post.target_group_id:
        return None
    group = (
        await session.execute(
            select(TargetGroup).where(TargetGroup.id == post.target_group_id)
        )
    ).scalar_one_or_none()
    return group.display_name_en if group else None


async def _case_title(session: AsyncSession, post: Post) -> str | None:
    if not post.case_id:
        return None
    case = (
        await session.execute(select(Case).where(Case.id == post.case_id))
    ).scalar_one_or_none()
    if case is None:
        return None
    # Cases have no title column; the narrative pattern is what identifies one to a
    # human, falling back to the group it concerns.
    return case.narrative_pattern or (
        case.target_group.display_name_en if case.target_group else None
    )


async def _prior_confirmed(session: AsyncSession, post: Post) -> int:
    """How many times this author has already been confirmed as hate speech."""
    if not post.tracked_account_id:
        return 0
    return (
        await session.scalar(
            select(func.count(ReviewerDecision.id))
            .join(Post, ReviewerDecision.post_id == Post.id)
            .where(
                Post.tracked_account_id == post.tracked_account_id,
                Post.id != post.id,
                ReviewerDecision.is_confirmed.is_(True),
            )
        )
    ) or 0


@router.post("/{queue_item_id}/submit")
async def submit_review(queue_item_id: str, submission: ReviewSubmission, session: AsyncSession = Depends(session_scope)):
    """Submit a human review decision for a queued item."""
    stmt = select(QueueItem).where(QueueItem.id == queue_item_id)
    result = await session.execute(stmt)
    q_item = result.scalar_one_or_none()
    
    if not q_item or not q_item.post_id:
        raise HTTPException(status_code=404, detail="Queue item not found")

    post_stmt = select(Post).where(Post.id == q_item.post_id)
    post_res = await session.execute(post_stmt)
    post = post_res.scalar_one()

    # Create decision record
    decision = ReviewerDecision(
        post_id=post.id,
        reviewer_id=submission.reviewer_id,
        original_hate_speech_flag=post.hate_speech_flag,
        original_multi_agent_trace=post.multi_agent_trace,
        is_confirmed=submission.is_confirmed,
        reviewer_rationale=submission.rationale
    )
    session.add(decision)

    queue_manager = QueueManager(session)
    
    if submission.is_confirmed:
        # T034: Evidence packaging would be triggered here in a full implementation
        log.info("Post confirmed as hate speech", post_id=post.id, reviewer=submission.reviewer_id)
        await queue_manager.mark_done(q_item, final_state=QueueState.DONE)
    else:
        # Falsely flagged, rejected
        log.info("Post rejected (false positive)", post_id=post.id, reviewer=submission.reviewer_id)
        await queue_manager.mark_done(q_item, final_state=QueueState.REJECTED)
        # Would also trigger learning loop regression evaluation here

    return {"status": "success", "decision_id": decision.id}

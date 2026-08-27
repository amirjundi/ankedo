"""Reviewer decisions become gold evaluation entries.

The loop this closes. A reviewer works through the queue, confirms or overturns each
verdict, and a ReviewerDecision row is written — and that was the end of it. The only
thing that changed the agent's behaviour was calibration, fitted from a static
`gold_eval.jsonl` that a person edited by hand. So the humans doing the actual
judgement work fed nothing back, and the agent's measured accuracy was frozen against
a file rather than against what it was getting right this month.

Each decision becomes one gold entry, keyed on the decision's id so re-running is
harmless. From there `ankedo eval calibrate` picks them up like any other gold data.

**The label is the reviewer's, not the agent's.** `is_confirmed` records whether the
human agreed, so the true label is the agent's flag when confirmed and its opposite
when overturned. Writing the agent's own verdict into the gold set would be circular —
the system would grade itself against its own past answers and improve forever without
ever being right.

**Overturned items are marked hard.** A case where the agent and a human disagreed is
worth more than a hundred it got trivially right, and `hard_case` is what lets the
evaluation weight it that way.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.comment import Comment
from src.models.gold_eval_entry import GoldEvalEntry
from src.models.post import Post
from src.models.reviewer_decision import ReviewerDecision

log = structlog.get_logger()

SOURCE = "reviewer"


def external_id_for(decision_id: str) -> str:
    """Stable and unique, so promoting twice updates rather than duplicates."""
    return f"review:{decision_id}"


def true_label(decision: ReviewerDecision) -> str:
    """What the human decided the item actually was.

    `original_hate_speech_flag` is the agent's verdict and `is_confirmed` is whether
    the reviewer agreed with it. Agreement means the agent's flag stands; disagreement
    means its opposite. A None flag — an item that reached review without a verdict —
    is treated as not-hate, since the reviewer confirming "nothing here" is a benign
    judgement.
    """
    agent_said = bool(decision.original_hate_speech_flag)
    actual = agent_said if decision.is_confirmed else not agent_said
    return "hate" if actual else "benign"


async def promote_reviewed_decisions(session: AsyncSession, *, limit: int = 500) -> int:
    """Turn reviewer decisions into gold entries. Returns how many were new."""
    existing = {
        row
        for row in (
            await session.execute(
                select(GoldEvalEntry.external_id).where(GoldEvalEntry.source == SOURCE)
            )
        ).scalars()
    }

    decisions = (
        await session.execute(
            select(ReviewerDecision).order_by(ReviewerDecision.created_at.desc()).limit(limit)
        )
    ).scalars().all()

    added = 0
    for decision in decisions:
        external_id = external_id_for(decision.id)
        if external_id in existing:
            continue

        text, parent, target_group, dialect = await _content(session, decision)
        if not (text or "").strip():
            # Nothing to evaluate against. A gold entry with no text would be scored
            # on every run and always mean nothing.
            continue

        session.add(
            GoldEvalEntry(
                external_id=external_id,
                text_content=text,
                parent_post_text=parent,
                target_group=target_group,
                dialect=dialect,
                label=true_label(decision),
                annotators=[decision.reviewer_id],
                # The agent was wrong here. These are the items an evaluation should
                # weight most heavily, and the ones a static gold file never contains.
                hard_case=not decision.is_confirmed,
                why=decision.reviewer_rationale,
                source=SOURCE,
            )
        )
        added += 1

    if added:
        await session.commit()
        log.info("Reviewer decisions promoted to the gold set", added=added)
    return added


async def _content(session: AsyncSession, decision: ReviewerDecision):
    """The text that was judged, and the post it sat under.

    The parent matters more here than anywhere else in the system: the whole premise
    is that a comment is judged against what it replies to, so a gold entry without
    its parent would be evaluated under conditions the agent never sees.
    """
    post = None
    if decision.post_id:
        post = (
            await session.execute(select(Post).where(Post.id == decision.post_id))
        ).scalar_one_or_none()

    if decision.comment_id:
        comment = (
            await session.execute(select(Comment).where(Comment.id == decision.comment_id))
        ).scalar_one_or_none()
        if comment is not None:
            if post is None and comment.post_id:
                post = (
                    await session.execute(select(Post).where(Post.id == comment.post_id))
                ).scalar_one_or_none()
            return (
                comment.text or "",
                post.content_text if post else None,
                getattr(post, "target_group_id", None) if post else None,
                getattr(post, "dialect", None) if post else None,
            )

    if post is not None:
        # A post judged on its own has no parent — that is the truth of it, not a
        # missing field, so it is left None rather than filled with the post itself.
        return (post.content_text or "", None, post.target_group_id, post.dialect)

    return ("", None, None, None)

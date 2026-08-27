"""
API Endpoints for generating reports.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta

from src.core.database import session_scope
from src.models.case import Case
from src.models.post import Post
from src.models.reviewer_decision import ReviewerDecision
from src.models.tracked_account import TrackedAccount

log = structlog.get_logger()
router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/summary")
async def get_summary_report(days: int = 30, session: AsyncSession = Depends(session_scope)):
    """T059: Generate summary report for a date range."""
    since_date = datetime.now(timezone.utc) - timedelta(days=days)
    since_iso = since_date.isoformat()
    
    # Cases count
    stmt_cases = select(func.count(Case.id)).where(Case.created_at >= since_iso)
    res_cases = await session.execute(stmt_cases)
    cases_count = res_cases.scalar()
    
    # Items flagged
    stmt_flagged = select(func.count(Post.id)).where(
        Post.created_at >= since_iso, 
        Post.hate_speech_flag == True # noqa: E712
    )
    res_flagged = await session.execute(stmt_flagged)
    flagged_count = res_flagged.scalar()
    
    # Confirmed items (assuming we had a confirmed flag on post or via reviewer decision, stubbing for now)
    
    # What reviewers actually decided. `confirmed_count` and `false_positive_rate`
    # were being rendered on the Reports page from a hardcoded fixture — 187 confirmed
    # and 12.3% — while this endpoint returned neither field. Both now come from
    # ReviewerDecision, which is the only place a human judgement is recorded.
    decisions = (
        await session.execute(
            select(
                func.count(ReviewerDecision.id),
                func.sum(case((ReviewerDecision.is_confirmed.is_(True), 1), else_=0)),
            ).where(ReviewerDecision.created_at >= since_date)
        )
    ).one()
    reviewed_total = decisions[0] or 0
    confirmed = int(decisions[1] or 0)

    # Of the items a reviewer looked at *because the agent flagged them*, the share
    # the reviewer overturned. Undefined rather than 0.0 when nothing has been
    # reviewed — reporting a 0% false-positive rate on no evidence would be a claim
    # about the classifier that nobody has earned.
    overturned = (
        await session.execute(
            select(func.count(ReviewerDecision.id)).where(
                ReviewerDecision.created_at >= since_date,
                ReviewerDecision.original_hate_speech_flag.is_(True),
                ReviewerDecision.is_confirmed.is_(False),
            )
        )
    ).scalar() or 0
    flagged_reviewed = (
        await session.execute(
            select(func.count(ReviewerDecision.id)).where(
                ReviewerDecision.created_at >= since_date,
                ReviewerDecision.original_hate_speech_flag.is_(True),
            )
        )
    ).scalar() or 0

    return {
        "report_period_days": days,
        "new_cases_count": cases_count,
        "items_flagged_count": flagged_count,
        "reviewed_count": reviewed_total,
        "confirmed_count": confirmed,
        "false_positive_rate": (
            round(100 * overturned / flagged_reviewed, 1) if flagged_reviewed else None
        ),
    }


@router.get("/repeat-offenders")
async def get_repeat_offenders_report(session: AsyncSession = Depends(session_scope)):
    """T060: Ranked list of tracked accounts by confirmed hate speech count."""
    # We aggregate Posts with hate_speech_flag=True group by tracked_account_id
    # Joined to the account so this returns handles. It returned bare
    # tracked_account_id UUIDs, which no page can display and no operator can read.
    stmt = (
        select(
            TrackedAccount.handle,
            TrackedAccount.platform,
            func.count(Post.id).label("offense_count"),
            func.max(Post.created_at).label("last_seen"),
        )
        .join(Post, Post.tracked_account_id == TrackedAccount.id)
        .where(Post.hate_speech_flag.is_(True))
        .group_by(TrackedAccount.handle, TrackedAccount.platform)
        .order_by(desc("offense_count"))
        .limit(50)
    )
    rows = (await session.execute(stmt)).all()

    return {
        "offenders": [
            {
                "handle": handle,
                "platform": platform,
                "offenses": count,
                "last_seen": last.isoformat() if hasattr(last, "isoformat") else last,
            }
            for handle, platform, count, last in rows
        ]
    }

@router.get("/stats/pages")
async def get_page_stats(session: AsyncSession = Depends(session_scope)):
    """Flagged items per account, worst first.

    Rewritten because the previous query grouped by `Post.author_id`, a column that
    does not exist on Post, and summed with `func.cast(flag, func.integer)`, which is
    not how cast is spelled. Any call raised. Nothing ever called it — the page that
    was supposed to had a hardcoded array instead — so the endpoint had been broken
    since it was written without anyone noticing.
    """
    stmt = (
        select(
            TrackedAccount.handle,
            TrackedAccount.platform,
            func.count(Post.id).label("total_posts"),
            func.sum(case((Post.hate_speech_flag.is_(True), 1), else_=0)).label("hate_posts"),
        )
        .join(Post, Post.tracked_account_id == TrackedAccount.id)
        .group_by(TrackedAccount.handle, TrackedAccount.platform)
        .order_by(desc("hate_posts"))
        .limit(20)
    )
    rows = (await session.execute(stmt)).all()

    return {
        "pages": [
            {
                "handle": handle,
                "platform": platform,
                "total_posts": total,
                "flagged": int(hate or 0),
            }
            for handle, platform, total, hate in rows
        ]
    }

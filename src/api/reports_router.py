"""
API Endpoints for generating reports.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta

from src.core.database import session_scope
from src.models.case import Case
from src.models.post import Post

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
    
    return {
        "report_period_days": days,
        "new_cases_count": cases_count,
        "items_flagged_count": flagged_count,
        # Other metrics would be aggregated here
    }


@router.get("/repeat-offenders")
async def get_repeat_offenders_report(session: AsyncSession = Depends(session_scope)):
    """T060: Ranked list of tracked accounts by confirmed hate speech count."""
    # We aggregate Posts with hate_speech_flag=True group by tracked_account_id
    stmt = (
        select(Post.tracked_account_id, func.count(Post.id).label("offense_count"))
        .where(Post.hate_speech_flag == True, Post.tracked_account_id.isnot(None)) # noqa: E712
        .group_by(Post.tracked_account_id)
        .order_by(func.count(Post.id).desc())
        .limit(50)
    )
    result = await session.execute(stmt)
    offenders = result.all()
    
    return {
        "repeat_offenders": [
            {"tracked_account_id": row.tracked_account_id, "offense_count": row.offense_count}
            for row in offenders
        ]
    }

@router.get("/stats/pages")
async def get_page_stats(session: AsyncSession = Depends(session_scope)):
    """T087: Implement per-page statistics endpoint."""
    # Stub: group by author/page and count total posts vs hate posts
    stmt = (
        select(Post.author_id, 
               func.count(Post.id).label("total_posts"),
               func.sum(func.cast(Post.hate_speech_flag, func.integer)).label("hate_posts")) # Stub integer cast
        .group_by(Post.author_id)
        .order_by(func.count(Post.id).desc())
        .limit(20)
    )
    result = await session.execute(stmt)
    pages = result.all()
    
    return {
        "page_stats": [
            {
                "author_id": p.author_id,
                "total_posts": p.total_posts,
                "hate_posts": p.hate_posts or 0
            } for p in pages
        ]
    }

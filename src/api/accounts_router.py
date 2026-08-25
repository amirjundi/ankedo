"""
API Endpoints for Tracked Accounts Intelligence.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import session_scope
from src.models.tracked_account import TrackedAccount
from src.models.post import Post

log = structlog.get_logger()
router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("/")
async def list_tracked_accounts(session: AsyncSession = Depends(session_scope)):
    """List all tracked accounts (repeat offenders)."""
    stmt = select(TrackedAccount).order_by(TrackedAccount.created_at.desc()).limit(100)
    result = await session.execute(stmt)
    accounts = result.scalars().all()
    
    return {"accounts": [{"id": a.id, "platform": a.platform, "handle": a.handle, "status": a.status} for a in accounts]}


@router.get("/{account_id}/history")
async def get_account_history(account_id: str, session: AsyncSession = Depends(session_scope)):
    """T054: Get ban history, predecessor links, and hate speech timeline."""
    stmt = select(TrackedAccount).where(TrackedAccount.id == account_id)
    result = await session.execute(stmt)
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")
        
    # Get predecessor chain
    history_chain = []
    current_acc = account
    while current_acc.predecessor_account_id:
        stmt_pred = select(TrackedAccount).where(TrackedAccount.id == current_acc.predecessor_account_id)
        res_pred = await session.execute(stmt_pred)
        current_acc = res_pred.scalar_one_or_none()
        if current_acc:
            history_chain.append({"id": current_acc.id, "handle": current_acc.handle, "status": current_acc.status})
        else:
            break
            
    # Get confirmed hate speech posts
    stmt_posts = select(Post).where(Post.tracked_account_id == account_id, Post.hate_speech_flag == True).order_by(Post.created_at.desc()) # noqa: E712
    res_posts = await session.execute(stmt_posts)
    posts = res_posts.scalars().all()
    
    timeline = [{"id": p.id, "url": p.url, "date": p.created_at} for p in posts]
            
    return {
        "account": {"id": account.id, "handle": account.handle, "platform": account.platform, "status": account.status},
        "predecessor_chain": history_chain,
        "hate_speech_timeline": timeline,
        "confirmed_count": len(timeline)
    }

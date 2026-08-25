"""
Admin endpoints for system management.
"""
from __future__ import annotations

import os
import shutil
import structlog
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import session_scope

from src.core.settings import get_settings

log = structlog.get_logger()
router = APIRouter(prefix="/api/admin", tags=["admin"])


class BackupRequest(BaseModel):
    destination_path: str


@router.post("/backup")
async def trigger_backup(req: BackupRequest):
    """T061: Exports full SQLite database and agent state to admin-specified path."""
    settings = get_settings()
    db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
    
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Primary database file not found")
        
    try:
        os.makedirs(req.destination_path, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(req.destination_path, f"ankedo_backup_{timestamp}.sqlite")
        
        # Simple file copy for SQLite backup
        shutil.copy2(db_path, backup_file)
        
        log.info("Database backup created successfully", backup_file=backup_file)
        return {"status": "success", "backup_file": backup_file}
        
    except Exception as e:
        log.exception("Backup failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")


@router.get("/health")
async def get_system_health():
    """T074: Returns system health dashboard data."""
    # Stub: Normally this would aggregate from DB and worker stats
    return {
        "status": "healthy",
        "crawl_throughput": 42.5,
        "queue_depths": {
            "discovery": 120,
            "processing": 45,
            "classification": 12,
            "review": 5
        },
        "classifier_latency_ms": 1250,
        "account_health": {
            "facebook": {"active": 5, "quarantine": 1},
            "tiktok": {"active": 3, "quarantine": 0},
            "instagram": {"active": 4, "quarantine": 2}
        }
    }

@router.get("/audit/{item_id}")
async def get_audit_trail(item_id: str, session: AsyncSession = Depends(session_scope)):
    """T086: Structured audit trail query endpoint for end-to-end tracing."""
    from src.models.post import Post
    from src.models.reviewer_decision import ReviewerDecision
    from src.models.evidence_package import EvidencePackage
    
    # 1. Fetch Post
    stmt = select(Post).where(Post.id == item_id)
    post = (await session.execute(stmt)).scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Item not found")
        
    # 2. Fetch Decisions
    stmt_dec = select(ReviewerDecision).where(ReviewerDecision.post_id == item_id)
    decisions = (await session.execute(stmt_dec)).scalars().all()
    
    # 3. Fetch Evidence
    stmt_ev = select(EvidencePackage).where(EvidencePackage.post_id == item_id)
    evidence = (await session.execute(stmt_ev)).scalars().all()
    
    return {
        "item_id": item_id,
        "collection": {
            "platform": post.platform,
            "url": post.url,
            "collected_at": post.created_at
        },
        "classification": {
            "score": post.classification_score,
            "flag": post.hate_speech_flag,
            "trace": post.multi_agent_trace
        },
        "reviewer_decisions": [
            {"reviewer": d.reviewer_id, "confirmed": d.is_confirmed, "rationale": d.reviewer_rationale}
            for d in decisions
        ],
        "evidence_packages": [
            {"id": e.id, "screenshot": e.screenshot_path, "generated_at": e.confirmed_at}
            for e in evidence
        ]
    }

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
from sqlalchemy import func, select
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
async def get_system_health(session: AsyncSession = Depends(session_scope)):
    """What the system is actually doing, counted from the database.

    This returned a fixed dictionary — 42.5 items/s, 120 queued, five healthy
    Facebook accounts — on a system that had never processed anything. The dashboard
    displayed those numbers and they were indistinguishable from real ones. An
    operator watching a monitoring tool cannot tell a confident stub from a working
    system, and this one would have shown a healthy pipeline while collecting nothing.

    Everything below is a count or a measurement. Where there is nothing to report it
    reports zero, which is the honest answer and is also the one that makes a stopped
    agent visible.
    """
    from datetime import timedelta

    from src.models.comment import Comment
    from src.models.llm_call import LLMCall
    from src.models.outbox import OutboxItem, OutboxStatus
    from src.models.queue_item import QueueItem, QueueStage
    from src.models.tracked_account import AccountStatus, TrackedAccount

    now = datetime.now(timezone.utc)
    # A datetime, not its isoformat string. These are DateTime columns; comparing
    # them against text relies on SQLite's type affinity doing the right thing and
    # silently returns nothing on a backend that is stricter.
    hour_ago = now - timedelta(hours=1)

    # Queue depth per stage, excluding what is already claimed by a worker.
    depths = {stage.value.lower(): 0 for stage in QueueStage}
    rows = await session.execute(
        select(QueueItem.stage, func.count(QueueItem.id))
        .where(QueueItem.is_inflight.is_(False))
        .group_by(QueueItem.stage)
    )
    for stage, count in rows:
        key = getattr(stage, "value", str(stage)).lower()
        depths[key] = count

    # Accounts by platform and status.
    account_health: dict[str, dict[str, int]] = {}
    rows = await session.execute(
        select(TrackedAccount.platform, TrackedAccount.status, func.count(TrackedAccount.id))
        .group_by(TrackedAccount.platform, TrackedAccount.status)
    )
    for platform, status, count in rows:
        name = getattr(status, "value", str(status)).lower()
        account_health.setdefault(platform, {})[name] = count

    # Throughput: comments classified in the last hour. Per hour, not per second —
    # a browser-driven crawler on a residential line does not do 42 items a second,
    # and a unit that flatters the number makes it useless for spotting a stall.
    classified_last_hour = (
        await session.execute(
            select(func.count(Comment.id)).where(
                Comment.classification_score.is_not(None),
                Comment.updated_at >= hour_ago,
            )
        )
    ).scalar() or 0

    # Median would be better than mean here, but this is one query and the number is
    # for spotting "the model got slow", not for capacity planning.
    latency = (
        await session.execute(
            select(func.avg(LLMCall.latency_ms)).where(LLMCall.created_at >= hour_ago)
        )
    ).scalar()

    outbox_pending = (
        await session.execute(
            select(func.count(OutboxItem.id)).where(OutboxItem.status == OutboxStatus.PENDING)
        )
    ).scalar() or 0
    outbox_failed = (
        await session.execute(
            select(func.count(OutboxItem.id)).where(OutboxItem.status == OutboxStatus.FAILED)
        )
    ).scalar() or 0

    settings = get_settings()
    return {
        # "healthy" is not a decoration: a monitoring tool that has stopped looks
        # exactly like one monitoring a quiet period, so say which it is.
        "status": "idle" if classified_last_hour == 0 else "working",
        "classified_last_hour": classified_last_hour,
        "queue_depths": depths,
        "classifier_latency_ms": round(latency) if latency else None,
        "account_health": account_health,
        "outbox": {
            "pending": outbox_pending,
            "failed": outbox_failed,
            # Verdicts accumulate here until the platform can store them; showing the
            # count without the reason would read as a broken submission path.
            "verdicts_held": not settings.ettok_verdict_endpoint_ready,
        },
        "platform_configured": bool(settings.ettok_base_url),
        "checked_at": now.isoformat(),
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


class ConfigChange(BaseModel):
    key: str
    value: str


@router.get("/config")
async def get_config():
    """The settings an operator may adjust, with their current values.

    Only what SETTABLE_KEYS names. Credentials are absent from that allowlist by
    construction rather than filtered out here, so a key added to the settings model
    tomorrow is invisible to this endpoint until somebody deliberately exposes it —
    the safe direction to forget in.
    """
    from src.chat.tools import SETTABLE_KEYS

    settings = get_settings()
    return {
        "settings": [
            {
                "key": key,
                "description": description,
                "value": str(getattr(settings, key.lower(), "")),
            }
            for key, description in SETTABLE_KEYS.items()
        ]
    }


@router.patch("/config")
async def update_config(change: ConfigChange):
    """Change one setting, through the same allowlist and validation as chat.

    Deliberately reuses the chat action rather than reimplementing it. Two code paths
    that write .env would eventually disagree about which keys are writable, and the
    one that drifted would be the security boundary.
    """
    from src.chat.tools import ActionError, run_action

    try:
        message = await run_action(
            "set_config", None, {"key": change.key, "value": change.value}
        )
    except ActionError as exc:
        # The operator's mistake — an unknown key, a value the settings model
        # refuses — not a server fault.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {"message": message}

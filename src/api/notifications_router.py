"""
API Endpoints for admin notifications.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from src.core.database import get_session
from src.models.agent_notification import AgentNotification, NotificationStatus

log = structlog.get_logger()
router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class AdminResponse(BaseModel):
    action_taken: str
    admin_id: str
    response_notes: str | None = None


@router.get("/")
async def get_notifications(session: AsyncSession = Depends(get_session)):
    """T063: Fetch pending notifications."""
    stmt = select(AgentNotification).where(AgentNotification.status == NotificationStatus.PENDING).order_by(AgentNotification.created_at.desc())
    result = await session.execute(stmt)
    notifications = result.scalars().all()
    
    return {"notifications": [
        {
            "id": n.id,
            "type": n.notification_type,
            "question": n.question,
            "urgency": n.urgency,
            "suggested_actions": n.suggested_actions
        } for n in notifications
    ]}


@router.post("/{notification_id}/respond")
async def respond_to_notification(notification_id: str, response: AdminResponse, session: AsyncSession = Depends(get_session)):
    """T063: Admin responds to a notification."""
    stmt = select(AgentNotification).where(AgentNotification.id == notification_id)
    result = await session.execute(stmt)
    notif = result.scalar_one_or_none()
    
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
        
    notif.status = NotificationStatus.RESOLVED
    notif.admin_response = response.action_taken
    notif.resolved_at = datetime.now(timezone.utc).isoformat()
    # In a full implementation, this triggers T066 orchestration loop handling
    
    await session.commit()
    log.info("Admin responded to notification", notif_id=notif.id, action=response.action_taken)
    return {"status": "success"}

"""AgentNotification — structured communication to the admin.

Shape follows spec 002 FR-C008: an alert carries its type, a context summary, an
impact level, and the reply options that let an admin respond from a phone.

`notification_type` is an open string rather than an enum. The previous enum listed
four values (Ambiguity, Resource, Discovery, Anomaly) and every actual caller used
something else — CapacityAlert, AccountBlocked, PlatformHalt, SelectorRepair,
HumanInterventionRequired — so it rejected every real notification. New alert kinds
appear as the agent gains capabilities, and a closed enum makes each one a migration.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class NotificationStatus(str, enum.Enum):
    PENDING = "Pending"
    RESOLVED = "Resolved"
    ESCALATED = "Escalated"
    # No answer within the timeout. The agent continues autonomously rather than
    # blocking forever, but the unanswered question stays on the record.
    TIMEOUT = "Timeout"


class AgentNotification(Base):
    __tablename__ = "agent_notifications"

    notification_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    context_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Numbered reply options, so an admin can answer "1" from Telegram (FR-C008).
    suggested_actions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    urgency: Mapped[str] = mapped_column(String(20), default="Medium", nullable=False, index=True)

    status: Mapped[str] = mapped_column(
        Enum(NotificationStatus), default=NotificationStatus.PENDING, nullable=False, index=True
    )
    admin_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AgentNotification {self.notification_type!r} "
            f"urgency={self.urgency!r} status={self.status!r}>"
        )

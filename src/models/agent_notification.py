"""AgentNotification — structured communication to the admin."""
from __future__ import annotations

import enum

from sqlalchemy import Enum, JSON
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class NotificationType(str, enum.Enum):
    AMBIGUITY = "Ambiguity"
    RESOURCE = "Resource"
    DISCOVERY = "Discovery"
    ANOMALY = "Anomaly"


class NotificationStatus(str, enum.Enum):
    PENDING = "Pending"
    RESOLVED = "Resolved"
    ESCALATED = "Escalated"


class AgentNotification(Base):
    __tablename__ = "agent_notifications"

    type: Mapped[str] = mapped_column(Enum(NotificationType), nullable=False)
    context_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(NotificationStatus), default=NotificationStatus.PENDING, nullable=False
    )

    def __repr__(self) -> str:
        return f"<AgentNotification type={self.type!r} status={self.status!r}>"

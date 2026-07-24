"""QueueItem — durable queue table for cross-stage transitions."""
from __future__ import annotations

import enum

from sqlalchemy import Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class QueueStage(str, enum.Enum):
    DISCOVERY = "Discovery"
    PROCESSING = "Processing"
    CLASSIFICATION = "Classification"
    REVIEW = "Review"


class QueueItem(Base):
    __tablename__ = "queue_items"

    stage: Mapped[str] = mapped_column(Enum(QueueStage), nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    
    # Payload references
    case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tracked_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    post_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    
    # State
    is_inflight: Mapped[bool] = mapped_column(default=False, nullable=False)
    locked_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    locked_by_worker: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return f"<QueueItem stage={self.stage!r} priority={self.priority!r}>"

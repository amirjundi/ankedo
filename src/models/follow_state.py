"""FollowState — which worker account follows which watch target.

Accounts get banned; that is routine, not exceptional. The knowledge worth keeping is
*which pages were being monitored*, not the identity doing the monitoring — so watch
targets are durable and worker accounts are disposable. When one is replaced, the
replacement rebuilds coverage from this table instead of a human reconstructing it.

Server-side is the right home for this (contract amendment §6); the local rows are a
working copy so the agent can act while offline.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class FollowStatus(str, enum.Enum):
    PENDING = "Pending"      # queued to follow, paced across warm-up
    REQUESTED = "Requested"  # request sent, awaiting approval on a private page
    FOLLOWING = "Following"
    FAILED = "Failed"
    UNFOLLOWED = "Unfollowed"


class FollowState(Base):
    __tablename__ = "follow_states"
    __table_args__ = (
        UniqueConstraint("worker_account_id", "tracked_account_id", name="uq_follow_pair"),
    )

    worker_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_worker_accounts.id"), nullable=False, index=True
    )
    tracked_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tracked_accounts.id"), nullable=False, index=True
    )

    status: Mapped[str] = mapped_column(
        Enum(FollowStatus), default=FollowStatus.PENDING, nullable=False, index=True
    )
    followed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Copied from the target so the follow queue can be ordered without a join —
    # if warm-up ends before the backlog does, the important pages are already covered.
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<FollowState worker={self.worker_account_id} target={self.tracked_account_id} {self.status}>"

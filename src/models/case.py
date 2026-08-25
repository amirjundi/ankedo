"""Case — incident-driven monitoring focus."""
from __future__ import annotations

import enum

from sqlalchemy import Enum, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class CaseState(str, enum.Enum):
    """Genuine state machine from FR-CM-2 — stays an enum; the transitions are code."""

    ACTIVE = "Active"
    COOLING = "Cooling"
    DORMANT = "Dormant"
    REACTIVATED = "Reactivated"


class Case(Base):
    __tablename__ = "cases"

    target_group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("target_groups.id"), nullable=False, index=True
    )
    narrative_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    dialect_scope: Mapped[str | None] = mapped_column(String(100), nullable=True)
    seed_posts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    watch_keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    target_pages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    state: Mapped[str] = mapped_column(
        Enum(CaseState), default=CaseState.ACTIVE, nullable=False
    )
    # Ordinal into severity_levels. An int rather than an FK-by-uuid so that ordered
    # comparisons ("severity >= 3") stay a plain column filter with no join.
    severity: Mapped[int] = mapped_column(Integer, default=2, nullable=False, index=True)
    # Timestamps for lifecycle transitions
    cooling_started_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dormant_started_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_activity_at: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationships
    target_group: Mapped["TargetGroup"] = relationship(lazy="selectin")  # noqa: F821
    tracked_accounts: Mapped[list["TrackedAccount"]] = relationship(  # noqa: F821
        back_populates="linked_case", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Case id={self.id!r} group_id={self.target_group_id!r} state={self.state!r}>"

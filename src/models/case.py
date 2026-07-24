"""Case — incident-driven monitoring focus."""
from __future__ import annotations

import enum

from sqlalchemy import Enum, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class CaseState(str, enum.Enum):
    ACTIVE = "Active"
    COOLING = "Cooling"
    DORMANT = "Dormant"
    REACTIVATED = "Reactivated"


class CaseSeverity(str, enum.Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class Case(Base):
    __tablename__ = "cases"

    target_group: Mapped[str] = mapped_column(String(255), nullable=False)
    narrative_pattern: Mapped[str | None] = mapped_column(Text, nullable=True)
    dialect_scope: Mapped[str | None] = mapped_column(String(100), nullable=True)
    seed_posts: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    watch_keywords: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    target_pages: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    state: Mapped[str] = mapped_column(
        Enum(CaseState), default=CaseState.ACTIVE, nullable=False
    )
    severity: Mapped[str] = mapped_column(
        Enum(CaseSeverity), default=CaseSeverity.MEDIUM, nullable=False
    )
    # Timestamps for lifecycle transitions
    cooling_started_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dormant_started_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_activity_at: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationships
    tracked_accounts: Mapped[list["TrackedAccount"]] = relationship(  # noqa: F821
        back_populates="linked_case", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Case id={self.id!r} group={self.target_group!r} state={self.state!r}>"

"""TrackedAccount — platform page/account on the continuous watch list."""
from __future__ import annotations

import enum

from sqlalchemy import Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class AccountStatus(str, enum.Enum):
    WARMUP = "Warmup"
    ACTIVE = "Active"
    BANNED = "Banned"
    SUSPENDED = "Suspended"


class AccountSource(str, enum.Enum):
    MANUAL = "Manual"
    AUTONOMOUS = "Autonomous"


class TrackedAccount(Base):
    __tablename__ = "tracked_accounts"

    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    handle: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    profile_picture_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    page_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(AccountStatus), default=AccountStatus.ACTIVE, nullable=False
    )
    source: Mapped[str] = mapped_column(
        Enum(AccountSource), default=AccountSource.MANUAL, nullable=False
    )
    linked_case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cases.id"), nullable=True
    )
    predecessor_account_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tracked_accounts.id"), nullable=True
    )
    ban_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_crawled_at: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Set each cycle by OrchestrationLoop._sync_workers from case state and account
    # health. Pacing is the scaling knob here: crawling faster than a platform
    # tolerates costs accounts, and an account costs far more than a slow cycle.
    crawl_interval_seconds: Mapped[int] = mapped_column(Integer, default=300, nullable=False)
    # Higher wins when the collector picks what to crawl next.
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)

    # Relationships
    linked_case: Mapped["Case | None"] = relationship(  # noqa: F821
        back_populates="tracked_accounts", foreign_keys=[linked_case_id]
    )
    posts: Mapped[list["Post"]] = relationship(  # noqa: F821
        back_populates="tracked_account", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<TrackedAccount {self.platform}/{self.handle!r} status={self.status!r}>"

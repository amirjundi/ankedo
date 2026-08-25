"""TrendSignal — hourly counts per group and platform, for spike detection.

Hate speech is episodic: it flares around an incident, cools, goes dormant, and
reactivates. Detecting the flare early is what makes monitoring useful rather than
retrospective, since spikes routinely precede offline violence.

One row per (target_group, platform, hour). The `observed` flag is what makes the
baseline honest: the host is sometimes switched off, and a gap in the data is not the
same as an hour with no hate speech. Averaging over wall-clock hours would read every
restart after an idle night as a spike.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class TrendSignal(Base):
    __tablename__ = "trend_signals"
    __table_args__ = (
        UniqueConstraint("target_group", "platform", "hour_bucket", name="uq_trend_bucket"),
    )

    target_group: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    # ISO hour, e.g. "2026-08-25T14"
    hour_bucket: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    items_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    items_flagged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Rate rather than raw count: flagging 40 of 100 comments is a different event
    # from flagging 40 of 40,000, and only the rate survives a change in crawl volume.
    hate_density: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # False for hours the agent was not running. Excluded from the baseline so
    # downtime cannot masquerade as calm.
    observed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    def __repr__(self) -> str:
        return f"<TrendSignal {self.target_group}/{self.platform} {self.hour_bucket} d={self.hate_density:.3f}>"

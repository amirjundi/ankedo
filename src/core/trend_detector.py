"""Spike detection over hourly hate-density signals.

EWMA baseline plus a z-score. Deliberately not a model: an NGO has to be able to
explain why the system escalated, and "mentions targeting this group are four standard
deviations above their own four-week average" is defensible in a way a learned
anomaly score is not. It also needs no training data, which matters when a new group
is added to the taxonomy.

The subtlety is the baseline. It is computed over **observed** hours only — hours the
agent was actually running. The host is sometimes on business hours, and treating an
idle night as zero activity would make every morning look like a spike, escalating
crawl rates exactly when nothing has happened.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.settings import get_settings
from src.models.trend_signal import TrendSignal

log = structlog.get_logger()


def hour_bucket(when: datetime | None = None) -> str:
    return (when or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H")


@dataclass
class Spike:
    target_group: str
    platform: str
    current: float
    baseline: float
    zscore: float
    observed_hours: int

    @property
    def multiple(self) -> float:
        return self.current / self.baseline if self.baseline else float("inf")

    def describe(self) -> str:
        return (
            f"{self.target_group} on {self.platform}: hate density {self.current:.1%} "
            f"vs baseline {self.baseline:.1%} ({self.multiple:.1f}x, z={self.zscore:.1f})"
        )


class TrendDetector:
    """Records hourly signals and reports spikes against each group's own baseline."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def record(
        self,
        *,
        target_group: str,
        platform: str,
        scanned: int,
        flagged: int,
        when: datetime | None = None,
    ) -> TrendSignal:
        """Add to the current hour's bucket, creating it if needed."""
        bucket = hour_bucket(when)
        row = (
            await self.session.execute(
                select(TrendSignal).where(
                    TrendSignal.target_group == target_group,
                    TrendSignal.platform == platform,
                    TrendSignal.hour_bucket == bucket,
                )
            )
        ).scalar_one_or_none()

        if row is None:
            # Counters set explicitly: SQLAlchemy applies `default=` at flush, so a
            # freshly constructed instance still holds None and `+=` would fail.
            row = TrendSignal(
                target_group=target_group,
                platform=platform,
                hour_bucket=bucket,
                observed=True,
                items_scanned=0,
                items_flagged=0,
                hate_density=0.0,
            )
            self.session.add(row)

        row.items_scanned += scanned
        row.items_flagged += flagged
        row.hate_density = row.items_flagged / row.items_scanned if row.items_scanned else 0.0
        row.observed = True

        await self.session.commit()
        return row

    async def detect(self) -> list[Spike]:
        """Return every (group, platform) currently spiking."""
        since = datetime.now(timezone.utc) - timedelta(days=self.settings.trend_baseline_days)
        cutoff = hour_bucket(since)
        current = hour_bucket()

        rows = (
            await self.session.execute(
                select(TrendSignal).where(
                    TrendSignal.hour_bucket >= cutoff,
                    TrendSignal.observed.is_(True),
                )
            )
        ).scalars().all()

        by_key: dict[tuple[str, str], list[TrendSignal]] = {}
        for row in rows:
            by_key.setdefault((row.target_group, row.platform), []).append(row)

        spikes: list[Spike] = []
        for (group, platform), series in by_key.items():
            series.sort(key=lambda r: r.hour_bucket)
            latest = series[-1]
            if latest.hour_bucket != current:
                continue  # nothing collected this hour; no claim to make

            history = series[:-1]
            if len(history) < self.settings.trend_min_history_hours:
                continue  # too little history for a baseline to mean anything

            baseline, deviation = _ewma(
                [r.hate_density for r in history], self.settings.trend_ewma_alpha
            )
            if latest.items_scanned < self.settings.trend_min_sample:
                continue  # a tiny sample makes a wild rate; not a spike

            # A flat baseline gives zero deviation, so a small floor keeps the z-score
            # finite rather than declaring every uptick infinitely significant.
            zscore = (latest.hate_density - baseline) / max(deviation, 0.01)

            if zscore >= self.settings.trend_zscore_threshold:
                spike = Spike(
                    target_group=group,
                    platform=platform,
                    current=latest.hate_density,
                    baseline=baseline,
                    zscore=zscore,
                    observed_hours=len(history),
                )
                spikes.append(spike)
                log.warning("Hate speech spike detected", detail=spike.describe())

        return spikes

    async def mark_unobserved(self, hours: list[str], target_group: str, platform: str) -> None:
        """Record hours the agent was not running, so they never enter a baseline."""
        for bucket in hours:
            self.session.add(
                TrendSignal(
                    target_group=target_group,
                    platform=platform,
                    hour_bucket=bucket,
                    observed=False,
                )
            )
        await self.session.commit()


def _ewma(values: list[float], alpha: float) -> tuple[float, float]:
    """Exponentially weighted mean and standard deviation.

    Recent hours weigh more, so a baseline adapts to a genuinely changed normal
    instead of treating a month-old quiet period as the reference forever.
    """
    if not values:
        return 0.0, 0.0

    mean = values[0]
    variance = 0.0
    for value in values[1:]:
        diff = value - mean
        mean += alpha * diff
        variance = (1 - alpha) * (variance + alpha * diff * diff)

    return mean, math.sqrt(variance)

"""Rebuild monitoring coverage on a replacement account.

When an account is banned, the operator provisions a new one and the agent restores
the same coverage. How that restoration is *paced* decides whether the replacement
survives its first week.

Three rules, each answering a specific way replacements get burned:

**Never bulk-follow.** An account that follows 200 pages in an hour is flagged
immediately. Follows are spread across the existing WARM_UP stage at a capped rate —
which is what warm-up is for.

**Never replay the banned account's order.** Following the same pages in the same
sequence is itself a fingerprint linking the new identity to the old one. The queue
is shuffled within priority bands.

**Priority first.** If warm-up ends before the backlog does, the pages that matter
are already covered.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.settings import get_settings
from src.models.agent_worker_account import AccountStage, AgentWorkerAccount
from src.models.follow_state import FollowState, FollowStatus
from src.models.tracked_account import AccountStatus, TrackedAccount

log = structlog.get_logger()


class FollowManager:
    """Plans and paces the follow backlog for worker accounts."""

    def __init__(self, session: AsyncSession, rng: random.Random | None = None):
        self.session = session
        self.settings = get_settings()
        self.rng = rng or random

    async def plan_for_account(self, worker: AgentWorkerAccount) -> int:
        """Queue every active target this worker does not yet follow.

        Idempotent — re-planning after a partial run adds only what is missing.
        """
        targets = (
            await self.session.execute(
                select(TrackedAccount).where(
                    TrackedAccount.platform == worker.platform,
                    TrackedAccount.status == AccountStatus.ACTIVE,
                )
            )
        ).scalars().all()

        existing = {
            row.tracked_account_id
            for row in (
                await self.session.execute(
                    select(FollowState).where(FollowState.worker_account_id == worker.id)
                )
            ).scalars()
        }

        queued = 0
        for target in targets:
            if target.id in existing:
                continue
            self.session.add(
                FollowState(
                    worker_account_id=worker.id,
                    tracked_account_id=target.id,
                    status=FollowStatus.PENDING,
                    priority=target.priority,
                )
            )
            queued += 1

        await self.session.commit()
        if queued:
            log.info("Follow backlog planned", worker=worker.username, queued=queued)
        return queued

    async def inherit_from(
        self, banned: AgentWorkerAccount, replacement: AgentWorkerAccount
    ) -> int:
        """Give a replacement the banned account's coverage.

        Only the *set* of targets carries over. Nothing about the original's timing or
        ordering does — that is what would link the two identities.
        """
        inherited = (
            await self.session.execute(
                select(FollowState).where(
                    FollowState.worker_account_id == banned.id,
                    FollowState.status.in_([FollowStatus.FOLLOWING, FollowStatus.REQUESTED]),
                )
            )
        ).scalars().all()

        existing = {
            row.tracked_account_id
            for row in (
                await self.session.execute(
                    select(FollowState).where(FollowState.worker_account_id == replacement.id)
                )
            ).scalars()
        }

        count = 0
        for row in inherited:
            if row.tracked_account_id in existing:
                continue
            self.session.add(
                FollowState(
                    worker_account_id=replacement.id,
                    tracked_account_id=row.tracked_account_id,
                    status=FollowStatus.PENDING,
                    priority=row.priority,
                )
            )
            count += 1

        await self.session.commit()
        log.info(
            "Coverage inherited",
            banned=banned.username,
            replacement=replacement.username,
            targets=count,
        )
        return count

    async def next_batch(self, worker: AgentWorkerAccount) -> list[FollowState]:
        """The follows to perform right now, or an empty list if the account is at quota.

        Quota is per day and applies during WARM_UP; an established account still
        follows gradually, just with more headroom.
        """
        quota = (
            self.settings.warmup_follows_per_day
            if worker.stage == AccountStage.WARM_UP
            else self.settings.active_follows_per_day
        )

        today = datetime.now(timezone.utc).date()
        done_today = (
            await self.session.execute(
                select(func.count(FollowState.id)).where(
                    FollowState.worker_account_id == worker.id,
                    FollowState.status == FollowStatus.FOLLOWING,
                    func.date(FollowState.followed_at) == today.isoformat(),
                )
            )
        ).scalar_one()

        remaining = quota - int(done_today)
        if remaining <= 0:
            log.debug("Follow quota reached", worker=worker.username, quota=quota)
            return []

        pending = (
            await self.session.execute(
                select(FollowState)
                .where(
                    FollowState.worker_account_id == worker.id,
                    FollowState.status == FollowStatus.PENDING,
                )
                .order_by(FollowState.priority.desc())
            )
        ).scalars().all()

        # Shuffle within each priority band: highest-value targets still go first,
        # but the sequence inside a band differs from any previous account's.
        batch: list[FollowState] = []
        for band in sorted({row.priority for row in pending}, reverse=True):
            in_band = [row for row in pending if row.priority == band]
            self.rng.shuffle(in_band)
            batch.extend(in_band)
            if len(batch) >= remaining:
                break

        return batch[:remaining]

    async def record(
        self, follow: FollowState, status: FollowStatus, reason: str | None = None
    ) -> None:
        follow.status = status
        follow.attempts += 1
        follow.failure_reason = reason
        if status == FollowStatus.FOLLOWING:
            follow.followed_at = datetime.now(timezone.utc)
        await self.session.commit()

    async def coverage_gaps(self, platform: str) -> list[TrackedAccount]:
        """Targets no healthy account follows.

        Invisible without server-side follow state, and it degrades monitoring
        silently — the agent keeps running while quietly watching less.
        """
        followed = select(FollowState.tracked_account_id).where(
            FollowState.status == FollowStatus.FOLLOWING
        )
        stmt = select(TrackedAccount).where(
            TrackedAccount.platform == platform,
            TrackedAccount.status == AccountStatus.ACTIVE,
            TrackedAccount.id.not_in(followed),
        )
        return list((await self.session.execute(stmt)).scalars().all())

"""
Discovery Engine - Autonomous lead following and exploration.
"""
from __future__ import annotations

import structlog
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.settings import get_settings
from src.core.watch_list_manager import WatchListManager
from src.notifications.dispatcher import NotificationDispatcher

log = structlog.get_logger()


class DiscoveryEngine:
    """Explores networks of hate speech actors autonomously."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()
        self.watch_list = WatchListManager(session)
        self.dispatcher = NotificationDispatcher(session)
        self._cycle_auto_add_count = 0

    def reset_cycle_limits(self):
        """T068: Reset per-cycle limits."""
        self._cycle_auto_add_count = 0

    async def _log_decision(self, event: str, inputs: dict, reasoning: str, action: str, notified: bool) -> None:
        """T069: Structured decision logging for all autonomous actions."""
        # NFR-AU-2: autonomous decisions retain the inputs and reasoning that produced
        # them. `decision=` rather than `event=` — structlog reserves `event` for the
        # message itself, and passing it as a keyword collides with the positional arg.
        log.info(
            "Autonomous Decision",
            decision=event,
            inputs=inputs,
            reasoning=reasoning,
            action=action,
            admin_notified=notified,
        )

    async def run_discovery(self) -> None:
        """
        T067: Main discovery loop that runs after classification batches.
        Analyzes recent classifications to find patterns.
        """
        self.reset_cycle_limits()
        log.info("Starting autonomous discovery")

        candidates = await self._authors_over_threshold()
        if not candidates:
            log.info("Autonomous discovery complete", candidates=0)
            return

        for platform, handle, flags in candidates:
            if self._cycle_auto_add_count < self.settings.auto_add_accounts_per_cycle:
                await self.watch_list.add_account(platform, handle)
                self._cycle_auto_add_count += 1

                await self._log_decision(
                    event="Repeated flagged items from one author",
                    inputs={"author": handle, "platform": platform, "recent_flags": flags},
                    reasoning=(
                        f"{flags} flagged items in the last "
                        f"{self.settings.discovery_window_days} days, threshold is "
                        f"{self.settings.discovery_flag_threshold}"
                    ),
                    action="Added to watch list",
                    notified=False,
                )
            else:
                # The per-cycle cap is a guardrail, not a queue. Past it a human
                # decides — the agent does not widen its own surveillance
                # indefinitely on the strength of its own findings.
                await self.dispatcher.send(
                    type_="DiscoveryApproval",
                    context={"target": handle, "platform": platform, "recent_flags": flags},
                    question=(
                        f"{handle} has {flags} flagged items. Add to the watch list? "
                        "This cycle's automatic limit is already used."
                    ),
                    urgency="Low",
                    suggested_actions=["Approve", "Reject"],
                )
                await self._log_decision(
                    event="Repeated flagged items from one author",
                    inputs={"author": handle, "platform": platform, "recent_flags": flags},
                    reasoning="Over threshold, but this cycle's auto-add limit is used",
                    action="Requested admin approval",
                    notified=True,
                )

        log.info("Autonomous discovery complete", candidates=len(candidates))

    async def _authors_over_threshold(self) -> list[tuple[str, str, int]]:
        """Authors with enough flagged items recently to be worth collecting more of.

        This replaces `high_hate_density = True  # Stub`, which was hardcoded. Every
        cycle it added a handle called `stub_discovered_user` to the watch list and
        wrote an autonomous decision recording `recent_flags: 4` — a number nothing
        had counted — into the audit trail that NFR-AU-2 exists to make trustworthy.
        On a system that had collected nothing it produced a watch list and a decision
        log full of a name that does not exist, once a minute, for as long as the
        agent ran.

        Now it counts. No flagged items means no candidates and nothing written, which
        is the honest output of a system that has not collected anything yet.
        """
        from datetime import timedelta

        from sqlalchemy import func, select

        from src.models.post import Post
        from src.models.tracked_account import TrackedAccount

        since = datetime.now(timezone.utc) - timedelta(
            days=self.settings.discovery_window_days
        )

        rows = (
            await self.session.execute(
                select(
                    Post.platform,
                    Post.author_name,
                    func.count(Post.id).label("flags"),
                )
                .where(
                    Post.hate_speech_flag.is_(True),
                    Post.author_name.is_not(None),
                    Post.created_at >= since,
                )
                .group_by(Post.platform, Post.author_name)
                .having(func.count(Post.id) >= self.settings.discovery_flag_threshold)
                .order_by(func.count(Post.id).desc())
                .limit(50)
            )
        ).all()

        if not rows:
            return []

        # Already-tracked handles are not discoveries. Re-adding is harmless, but it
        # re-logs the same decision every cycle, which is how a decision log stops
        # being read.
        tracked = {
            (a.platform, a.handle)
            for a in (await self.session.execute(select(TrackedAccount))).scalars()
        }
        return [
            (platform, handle, flags)
            for platform, handle, flags in rows
            if (platform, handle) not in tracked
        ]

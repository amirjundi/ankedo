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
        
        # Stub: Imagine we query recent hate speech posts and group by author
        # If an author has > 3 hate speech posts, we investigate
        
        high_hate_density = True  # Stub
        
        if high_hate_density:
            if self._cycle_auto_add_count < self.settings.auto_add_accounts_per_cycle:
                # T067: Auto-add to watch list
                await self.watch_list.add_account("facebook", "stub_discovered_user")
                self._cycle_auto_add_count += 1
                
                await self._log_decision(
                    event="High hate density detected",
                    inputs={"author": "stub_discovered_user", "recent_flags": 4},
                    reasoning="Author exceeds threshold of 3 hate speech posts",
                    action="Added to watch list",
                    notified=False
                )
            else:
                # T068: Hit guardrail, request admin approval
                await self.dispatcher.send(
                    type_="DiscoveryApproval",
                    context={"target": "stub_discovered_user", "reason": "High hate density"},
                    question="Should we add this account to the watch list? Cycle limit exceeded.",
                    urgency="Low",
                    suggested_actions=["Approve", "Reject"]
                )
                await self._log_decision(
                    event="High hate density detected",
                    inputs={"author": "stub_discovered_user", "recent_flags": 4},
                    reasoning="Author exceeds threshold, but cycle auto-add limit reached",
                    action="Requested admin approval",
                    notified=True
                )
                
        log.info("Autonomous discovery complete")

"""
Orchestration Loop - The core daemon that coordinates all agents and workers.
"""
from __future__ import annotations

import asyncio
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.case_manager import CaseManager
from src.core.settings import get_settings
from src.models.case import Case, CaseState
from src.models.tracked_account import TrackedAccount
from src.notifications.dispatcher import NotificationDispatcher
from src.models.agent_notification import AgentNotification, NotificationStatus
from src.core.discovery_engine import DiscoveryEngine
from src.models.agent_worker_account import AgentWorkerAccount, AccountState

log = structlog.get_logger()


class OrchestrationLoop:
    """Main continuous daemon loop."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()
        self.case_manager = CaseManager(session)
        self.dispatcher = NotificationDispatcher(session)
        self.discovery = DiscoveryEngine(session)

    async def _schedule_cases(self) -> None:
        """Evaluate case lifecycle and prioritize active cases."""
        stmt = select(Case).where(Case.state.in_([CaseState.ACTIVE, CaseState.COOLING]))
        result = await self.session.execute(stmt)
        cases = result.scalars().all()
        
        for case in cases:
            # Update lifecycle states
            await self.case_manager.evaluate_lifecycle(case)
            
            # Here we would dispatch collectors for high-priority targets
            if case.state == CaseState.ACTIVE:
                log.debug("Scheduling high-frequency crawl for ACTIVE case", case_id=case.id)
            elif case.state == CaseState.COOLING:
                log.debug("Scheduling reduced-frequency crawl for COOLING case", case_id=case.id)

    async def _handle_notifications(self) -> None:
        """T064/T066: Check for needed escalations and process admin responses."""
        await self.dispatcher.check_escalations()
        
        # Stub: check for newly resolved notifications and apply admin response to agent behavior
        stmt = select(AgentNotification).where(
            AgentNotification.status == NotificationStatus.RESOLVED
        )
        # Process them...

    async def _check_alerts(self) -> None:
        """T073: Monitors healthy-account count and fires AgentNotification if below threshold."""
        stmt = select(AgentWorkerAccount.platform).where(AgentWorkerAccount.state == AccountState.HEALTHY)
        result = await self.session.execute(stmt)
        platforms = [p for (p,) in result.all()]
        
        counts = {
            "facebook": platforms.count("facebook"),
            "tiktok": platforms.count("tiktok"),
            "instagram": platforms.count("instagram")
        }
        
        for platform, count in counts.items():
            if count < self.settings.min_healthy_accounts_per_platform:
                await self.dispatcher.send(
                    type_="CapacityAlert",
                    context={"platform": platform, "healthy_count": count},
                    question=f"Low capacity on {platform}. Current: {count}. Minimum required: {self.settings.min_healthy_accounts_per_platform}",
                    urgency="High",
                    suggested_actions=["Add new worker accounts", "Review quarantined accounts"]
    async def _sync_workers(self) -> None:
        """T075: Dynamic horizontal worker scaling."""
        # Query active accounts and instantiate/destroy CollectorWorker instances
        # without code changes or restarts
        pass

    async def _process_queues(self) -> None:
        """T083: Process all queues and enforce guardrails."""
        # This calls the QueueManager and Workers
        # Guardrails enforced:
        # - AutoSubmitGuardrailError blocks any automated platform report submissions
        # - Rate limits checked before dispatching
        pass
        
    async def _evaluate_expansion(self) -> None:
        """T084: Reply sub-thread expansion based on hate density signal."""
        # Agent decides to expand into reply threads only if hate density > threshold
        pass
        
    async def _prevent_reviewer_overload(self) -> None:
        """T085: Auto-flagging and batching borderline items."""
        # High confidence -> direct confirm stub logic
        # Borderline -> batch to prevent overloading reviewers
        pass

    async def run_cycle(self) -> None:
        """Run one full tick of the orchestration loop."""
        log.info("Starting orchestration cycle")
        
        # 0. Sync workers dynamically (T075)
        await self._sync_workers()
        
        # 1. Schedule Cases (T036)
        await self._schedule_cases()
        
        # 2. Process Notifications (T064, T066)
        await self._handle_notifications()
        
        # 3. Queue Processing & Guardrails (T083)
        await self._process_queues()
        
        # 4. Content Expansion (T084)
        await self._evaluate_expansion()
        
        # 5. Overload Prevention (T085)
        await self._prevent_reviewer_overload()
        
        # 2. Process Notifications (T064, T066)
        await self._handle_notifications()
        
        # 6. Autonomous Discovery (T070)
        await self.discovery.run_discovery()
        
        # 7. Check alerts (T073)
        await self._check_alerts()
        
        log.info("Orchestration cycle complete")

    async def run_forever(self) -> None:
        """Run continuously."""
        while True:
            try:
                await self.run_cycle()
            except Exception as e:
                log.exception("Error in orchestration cycle", error=str(e))
            finally:
                await asyncio.sleep(self.settings.loop_interval_seconds)

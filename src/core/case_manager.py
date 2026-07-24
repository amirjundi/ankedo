"""
Case Manager - Manages the lifecycle of incident-driven Cases.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.case import Case, CaseState, CaseSeverity
from src.core.settings import get_settings

log = structlog.get_logger()


class CaseManager:
    """CRUD and state machine logic for Cases."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def create_case(self, target_group: str, seed_posts: list[str], watch_keywords: list[str], severity: CaseSeverity = CaseSeverity.MEDIUM) -> Case:
        """Create a new Active case."""
        case = Case(
            target_group=target_group,
            seed_posts=seed_posts,
            watch_keywords=watch_keywords,
            state=CaseState.ACTIVE,
            severity=severity,
            last_activity_at=datetime.now(timezone.utc).isoformat()
        )
        self.session.add(case)
        await self.session.commit()
        log.info("Created new case", case_id=case.id, target_group=target_group)
        return case

    async def evaluate_lifecycle(self, case: Case) -> None:
        """Evaluate and transition case state based on time thresholds."""
        now = datetime.now(timezone.utc)
        
        if not case.last_activity_at:
            return
            
        last_activity = datetime.fromisoformat(case.last_activity_at.replace("Z", "+00:00"))
        hours_since_activity = (now - last_activity).total_seconds() / 3600
        days_since_activity = (now - last_activity).total_seconds() / 86400
        
        # ACTIVE -> COOLING
        if case.state == CaseState.ACTIVE and hours_since_activity > self.settings.cooling_threshold_hours:
            case.state = CaseState.COOLING
            case.cooling_started_at = now.isoformat()
            log.info("Case transitioned to COOLING", case_id=case.id)
            
        # COOLING -> DORMANT
        elif case.state == CaseState.COOLING and days_since_activity > self.settings.dormant_threshold_days:
            case.state = CaseState.DORMANT
            case.dormant_started_at = now.isoformat()
            log.info("Case transitioned to DORMANT", case_id=case.id)
            
        self.session.add(case)
        await self.session.commit()

    async def propose_reactivation(self, case: Case, trigger_reason: str) -> None:
        """T037: Propose reactivation when dormant keywords resurface."""
        if case.state == CaseState.DORMANT:
            log.warning("Proposing case reactivation", case_id=case.id, reason=trigger_reason)
            # In full implementation, this sends an AgentNotification. For now, auto-reactivate.
            case.state = CaseState.REACTIVATED
            case.last_activity_at = datetime.now(timezone.utc).isoformat()
            self.session.add(case)
            await self.session.commit()

    async def handle_retroactive_reclassification(self) -> None:
        """T088: Flag items matching new trope patterns for re-review."""
        log.info("Checking for retroactive reclassification opportunities")
        # When trope dictionary is updated, we would scan older unflagged posts
        # and if they hit the new tropes, enqueue them to Review Queue with a 
        # 'reclassification_proposed' flag, so they are not auto-reclassified.
        pass

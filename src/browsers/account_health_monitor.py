"""
Account Health Monitor - Detects blocks and challenges.
"""
from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.models.agent_worker_account import AgentWorkerAccount, AccountStage, AccountState
from src.notifications.dispatcher import NotificationDispatcher

log = structlog.get_logger()


class AccountHealthMonitor:
    """Monitors worker accounts for blocks and CAPTCHAs (T071)."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.dispatcher = NotificationDispatcher(session)

    async def report_block(self, account_id: str, reason: str) -> None:
        """Report a block or challenge on an account."""
        stmt = select(AgentWorkerAccount).where(AgentWorkerAccount.id == account_id)
        result = await self.session.execute(stmt)
        account = result.scalar_one_or_none()
        
        if not account:
            return
            
        log.warning("Account blocked or challenged", account_id=account_id, reason=reason)
        account.state = AccountState.BLOCKED
        account.stage = AccountStage.QUARANTINE
        
        await self.session.commit()
        
        # Notify admin
        await self.dispatcher.send(
            type_="AccountBlocked",
            context={"account_id": account_id, "platform": account.platform, "reason": reason},
            question="Worker account has been blocked and quarantined. Review needed.",
            urgency="High",
            suggested_actions=["Review Block", "Rotate Proxy", "Retire Account"]
        )

    async def check_platform_halt(self, platform: str) -> None:
        """T089: Add all-accounts-blocked platform halt."""
        stmt = select(AgentWorkerAccount).where(
            AgentWorkerAccount.platform == platform,
            AgentWorkerAccount.state == AccountState.HEALTHY
        )
        result = await self.session.execute(stmt)
        healthy = result.scalars().all()
        
        if not healthy:
            log.critical("All accounts for platform blocked. Halting crawling.", platform=platform)
            await self.dispatcher.send(
                type_="PlatformHalt",
                context={"platform": platform},
                question=f"All accounts for {platform} are blocked. Crawling paused. Please provision new accounts.",
                urgency="Critical",
                suggested_actions=["Provision New Accounts", "Review Proxies"]
            )
            # Orchestrator will see 0 capacity and not schedule platform


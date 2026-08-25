"""
Account Lifecycle Manager - Manages the stages of an agent worker account.
"""
from __future__ import annotations

import structlog
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.models.agent_worker_account import AgentWorkerAccount, AccountStage
from src.core.settings import get_settings

log = structlog.get_logger()


class AccountLifecycleManager:
    """Manages the stages of account life (T072)."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def evaluate_accounts(self) -> None:
        """Evaluate and transition accounts between stages."""
        stmt = select(AgentWorkerAccount)
        result = await self.session.execute(stmt)
        accounts = result.scalars().all()
        
        now = datetime.now(timezone.utc)
        
        for account in accounts:
            if not account.last_used_at:
                continue
                
            last_used = datetime.fromisoformat(account.last_used_at.replace("Z", "+00:00"))
            hours_since_use = (now - last_used).total_seconds() / 3600
            
            # WARM_UP to ACTIVE transition
            # trust_score is a 0-100 int; the old `> 0.5` promoted anything above zero
            if (
                account.stage == AccountStage.WARM_UP
                and account.trust_score >= self.settings.warmup_trust_threshold
            ):
                account.stage = AccountStage.ACTIVE
                log.info("Account transitioned to ACTIVE", account_id=account.id)

            # RECOVERY to ACTIVE transition
            elif (
                account.stage == AccountStage.RECOVERY
                and hours_since_use > self.settings.recovery_idle_hours
            ):
                account.stage = AccountStage.ACTIVE
                log.info("Account recovered and transitioned to ACTIVE", account_id=account.id)
                
        await self.session.commit()

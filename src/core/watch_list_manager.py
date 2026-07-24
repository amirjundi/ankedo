"""
Watch List Manager - Manages the continuous watch list of TrackedAccounts.
"""
from __future__ import annotations

from datetime import datetime, timezone
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.tracked_account import TrackedAccount, AccountStatus, AccountSource

log = structlog.get_logger()


class WatchListManager:
    """Manages continuous monitoring of known bad actors."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_account(self, platform: str, handle: str, display_name: str | None = None, source: AccountSource = AccountSource.AUTONOMOUS, linked_case_id: str | None = None) -> TrackedAccount:
        """Add a new account to the watch list."""
        stmt = select(TrackedAccount).where(
            TrackedAccount.platform == platform,
            TrackedAccount.handle == handle
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        
        if existing:
            log.info("Account already on watch list", platform=platform, handle=handle)
            return existing
            
        account = TrackedAccount(
            platform=platform,
            handle=handle,
            display_name=display_name,
            source=source,
            linked_case_id=linked_case_id,
            status=AccountStatus.ACTIVE,
            first_seen_at=datetime.now(timezone.utc).isoformat()
        )
        self.session.add(account)
        await self.session.commit()
        log.info("Added account to watch list", platform=platform, handle=handle)
        return account

    async def link_new_identity(self, old_account_id: str, new_platform: str, new_handle: str) -> TrackedAccount:
        """Link a new account to a banned predecessor (T039)."""
        stmt = select(TrackedAccount).where(TrackedAccount.id == old_account_id)
        result = await self.session.execute(stmt)
        old_account = result.scalar_one_or_none()
        
        if not old_account:
            raise ValueError(f"Predecessor account {old_account_id} not found")
            
        old_account.status = AccountStatus.BANNED
        self.session.add(old_account)
        
        new_account = await self.add_account(
            platform=new_platform,
            handle=new_handle,
            source=AccountSource.MANUAL,
            linked_case_id=old_account.linked_case_id
        )
        new_account.predecessor_account_id = old_account.id
        self.session.add(new_account)
        await self.session.commit()
        
        log.info("Linked new identity for banned account", old_id=old_account.id, new_id=new_account.id)
        return new_account

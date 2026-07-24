"""
Tracked Account Service - Intelligence on repeat offenders.
"""
from __future__ import annotations

from datetime import datetime, timezone
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.tracked_account import TrackedAccount, AccountSource, AccountStatus
from src.core.watch_list_manager import WatchListManager

log = structlog.get_logger()


class TrackedAccountService:
    """Manages the intelligence around tracked accounts."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.watch_list = WatchListManager(session)

    async def handle_confirmed_flag(self, platform: str, handle: str, display_name: str | None = None, linked_case_id: str | None = None) -> TrackedAccount:
        """
        Called when a post/comment is confirmed as hate speech.
        T051: Auto-adds account to continuous watch list if not already there.
        """
        # T053: Automatic case association is handled by passing linked_case_id
        return await self.watch_list.add_account(
            platform=platform,
            handle=handle,
            display_name=display_name,
            source=AccountSource.AUTONOMOUS,
            linked_case_id=linked_case_id
        )

    async def link_predecessor(self, new_account_id: str, old_account_id: str) -> TrackedAccount:
        """
        T052: Bot manager links a newly discovered account to a banned predecessor.
        This preserves ban history.
        """
        stmt = select(TrackedAccount).where(TrackedAccount.id == new_account_id)
        result = await self.session.execute(stmt)
        new_account = result.scalar_one_or_none()
        
        stmt_old = select(TrackedAccount).where(TrackedAccount.id == old_account_id)
        result_old = await self.session.execute(stmt_old)
        old_account = result_old.scalar_one_or_none()
        
        if not new_account or not old_account:
            raise ValueError("Accounts not found")
            
        new_account.predecessor_account_id = old_account.id
        
        # Inherit the case link if applicable
        if old_account.linked_case_id and not new_account.linked_case_id:
            new_account.linked_case_id = old_account.linked_case_id
            
        self.session.add(new_account)
        
        # Ensure old is marked banned
        old_account.status = AccountStatus.BANNED
        self.session.add(old_account)
        
        await self.session.commit()
        log.info("Linked predecessor account", new_id=new_account.id, old_id=old_account.id)
        
        return new_account

"""
Notification Dispatcher - Handles agent-to-admin communications.
"""
from __future__ import annotations

import structlog
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from src.models.agent_notification import AgentNotification, NotificationStatus
from src.core.settings import get_settings

log = structlog.get_logger()


class NotificationDispatcher:
    """Dispatches notifications and handles escalations."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def send(self, type_: str, context: dict, question: str, urgency: str = "Medium", suggested_actions: list[str] | None = None) -> AgentNotification:
        """T062: Send a structured notification to the admin."""
        notif = AgentNotification(
            notification_type=type_,
            context_data=context,
            question=question,
            suggested_actions=suggested_actions or [],
            urgency=urgency,
            status=NotificationStatus.PENDING,
        )  # created_at is set by Base as a real datetime
        self.session.add(notif)
        await self.session.commit()
        
        # Dispatch to Telegram if bot is running
        try:
            from src.chat.channels.telegram_channel import bot, authorized_chat_id
            if bot and authorized_chat_id:
                msg = f"🚨 **Alert ({urgency}): {type_}**\n\n{question}\n\nContext: {context}"
                if suggested_actions:
                    msg += f"\n\nSuggested Actions: {', '.join(suggested_actions)}"
                await bot.send_message(authorized_chat_id, msg, parse_mode="Markdown")
                log.info("Dispatched admin notification to Telegram", notif_id=notif.id)
            else:
                log.info("Telegram bot not active, notification logged to DB only", notif_id=notif.id)
        except ImportError:
            log.info("Dispatched admin notification (DB only)", notif_id=notif.id)
            
        return notif

    async def check_escalations(self) -> None:
        """T065: Escalate unacknowledged notifications."""
        stmt = select(AgentNotification).where(AgentNotification.status == NotificationStatus.PENDING)
        result = await self.session.execute(stmt)
        pending = result.scalars().all()
        
        now = datetime.now(timezone.utc)
        
        for notif in pending:
            created = notif.created_at
            if created.tzinfo is None:  # SQLite returns naive datetimes
                created = created.replace(tzinfo=timezone.utc)
            minutes_pending = (now - created).total_seconds() / 60

            # FR-C011: raise urgency on an unanswered alert, then stop waiting.
            # Checked timeout-first — with the order reversed, an alert that is
            # already High can never reach TIMEOUT and waits forever.
            if minutes_pending > self.settings.notification_timeout_minutes:
                notif.status = NotificationStatus.TIMEOUT
                log.error(
                    "Notification timed out with no response",
                    notif_id=notif.id,
                    minutes=int(minutes_pending),
                )
                # The agent carries on autonomously; the unanswered question stays
                # on the record rather than blocking the loop.
            elif (
                minutes_pending > self.settings.escalation_interval_minutes
                and notif.urgency != "High"
            ):
                notif.urgency = "High"
                log.warning("Escalating notification urgency", notif_id=notif.id)

        await self.session.commit()

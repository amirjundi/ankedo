"""Telegram Bot Integration using aiogram."""
from __future__ import annotations

import structlog
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from src.core.database import get_session_factory
from src.models.tracked_account import TrackedAccount, AccountSource, AccountStatus
from src.core.settings import get_settings

log = structlog.get_logger()
dp = Dispatcher()

# Globals to be set on startup
bot: Bot | None = None
authorized_chat_id: int | None = None


def is_authorized(message: Message) -> bool:
    """Check if the message comes from the authorized admin."""
    if authorized_chat_id and message.chat.id == authorized_chat_id:
        return True
    log.warning("Unauthorized access attempt", chat_id=message.chat.id, user=message.from_user.username)
    return False


@dp.message(CommandStart())
async def cmd_start(message: Message):
    if not is_authorized(message):
        return
    await message.answer("AnkEdo Hate Speech Monitor is online.\n\nCommands:\n/allowlist - View monitored IG accounts\n/add_ig <handle> - Add IG account\n/remove_ig <handle> - Remove IG account")


@dp.message(Command("allowlist"))
async def cmd_allowlist(message: Message):
    if not is_authorized(message):
        return
        
    async with get_session_factory()() as session:
        stmt = select(TrackedAccount).where(
            TrackedAccount.platform == "instagram",
            TrackedAccount.status.in_([AccountStatus.ACTIVE, AccountStatus.WARMUP])
        )
        result = await session.execute(stmt)
        accounts = result.scalars().all()
        
        if not accounts:
            await message.answer("No Instagram accounts are currently on the allow-list.")
            return
            
        text = "📋 **Instagram Monitoring Allow-List:**\n\n"
        for acc in accounts:
            text += f"• @{acc.handle} ({acc.status})\n"
            
        await message.answer(text, parse_mode="Markdown")


@dp.message(Command("add_ig"))
async def cmd_add_ig(message: Message):
    if not is_authorized(message):
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /add_ig <handle>")
        return
        
    handle = parts[1].strip().replace("@", "")
    
    async with get_session_factory()() as session:
        # Check if exists
        stmt = select(TrackedAccount).where(TrackedAccount.platform == "instagram", TrackedAccount.handle == handle)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        
        if existing:
            await message.answer(f"Account @{handle} is already in the database with status: {existing.status}")
            return
            
        new_account = TrackedAccount(
            platform="instagram",
            handle=handle,
            status=AccountStatus.ACTIVE,
            # We don't have AccountSource.MANUAL in the literal model, but we will assume it's created.
            # Actually the model does have AccountSource.MANUAL.
        )
        session.add(new_account)
        await session.commit()
        
        log.info("Instagram account added via Telegram", handle=handle)
        await message.answer(f"✅ Added @{handle} to the Instagram monitoring allow-list.")


@dp.message(Command("remove_ig"))
async def cmd_remove_ig(message: Message):
    if not is_authorized(message):
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /remove_ig <handle>")
        return
        
    handle = parts[1].strip().replace("@", "")
    
    async with get_session_factory()() as session:
        stmt = select(TrackedAccount).where(TrackedAccount.platform == "instagram", TrackedAccount.handle == handle)
        existing = (await session.execute(stmt)).scalar_one_or_none()
        
        if not existing:
            await message.answer(f"Account @{handle} not found in the allow-list.")
            return
            
        await session.delete(existing)
        await session.commit()
        
        log.info("Instagram account removed via Telegram", handle=handle)
        await message.answer(f"❌ Removed @{handle} from the Instagram monitoring allow-list.")


# ── Free-text conversation ───────────────────────────────────────────────────
# Registered last, so it only sees messages the command handlers above did not
# claim. The four commands stay: /add_ig is one tap and always does exactly one
# thing, which is worth more on a phone than phrasing a sentence.

# A change the agent proposed, per chat, waiting on a yes. Held in memory rather
# than the database: a pending confirmation should not survive a restart, because
# the operator would be agreeing to something they can no longer see.
_pending: dict[int, dict] = {}

_YES = {"yes", "y", "ok", "okay", "confirm", "do it", "نعم", "تمام", "بەڵێ"}
_NO = {"no", "n", "cancel", "stop", "لا", "نه", "نەخێر"}


@dp.message()
async def handle_conversation(message: Message):
    if not is_authorized(message):
        return

    text = (message.text or "").strip()
    if not text:
        return

    chat_id = message.chat.id
    waiting = _pending.get(chat_id)

    # An answer to a pending confirmation is not a new request.
    if waiting:
        answer = text.lower()
        if answer in _YES:
            _pending.pop(chat_id, None)
            await _reply_from_agent(message, confirm=waiting)
            return
        if answer in _NO:
            _pending.pop(chat_id, None)
            await message.answer("Cancelled — nothing was changed.")
            return
        # Anything else replaces the pending change rather than being taken as
        # consent to it. Silence is not agreement, and neither is a new question.
        _pending.pop(chat_id, None)
        await message.answer("[dropped the pending change]")

    await _reply_from_agent(message, prompt=text)


async def _reply_from_agent(message: Message, *, prompt: str = "", confirm: dict | None = None):
    """Run one turn through the shared agent and answer."""
    from src.chat.agent import ChatAgent

    session_factory = get_session_factory()
    try:
        async with session_factory() as session:
            agent = ChatAgent(session)
            result = await agent.confirm(confirm) if confirm else await agent.handle(prompt)
    except Exception as exc:  # a broken turn must not kill the polling loop
        log.exception("Telegram chat turn failed")
        await message.answer(f"Something went wrong: {exc}")
        return

    if result.pending:
        _pending[message.chat.id] = result.pending
        await message.answer(f"{result.text}\n\nReply yes to apply, or no to cancel.")
        return

    # Telegram rejects messages over 4096 characters.
    body = result.text or "(no reply)"
    for start in range(0, len(body), 3900):
        await message.answer(body[start:start + 3900])


async def start_telegram_bot(token: str, admin_chat_id: int):
    """Start the aiogram bot polling."""
    global bot, authorized_chat_id
    authorized_chat_id = admin_chat_id
    bot = Bot(token=token)
    log.info("Starting Telegram Bot Polling...")
    await dp.start_polling(bot)

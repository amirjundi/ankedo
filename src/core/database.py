"""
Database initialization and session management.
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.base import Base, _get_engine, get_session_factory
# Import all models here so metadata is populated before create_all()
from src.models.agent_config import AgentConfig  # noqa
from src.models.agent_notification import AgentNotification  # noqa
from src.models.agent_worker_account import AgentWorkerAccount  # noqa
from src.models.case import Case  # noqa
from src.models.channel_config import ChannelConfig  # noqa
from src.models.chat_message import ChatMessage  # noqa
from src.models.comment import Comment  # noqa
from src.models.evidence_package import EvidencePackage  # noqa
from src.models.follow_state import FollowState  # noqa
from src.models.gold_eval_entry import GoldEvalEntry  # noqa
from src.models.lexicon_entry import LexiconEntry  # noqa
from src.models.llm_call import LLMCall  # noqa
from src.models.outbox import OutboxItem  # noqa
from src.models.post import Post  # noqa
from src.models.queue_item import QueueItem  # noqa
from src.models.reviewer_decision import ReviewerDecision  # noqa
from src.models.severity_level import SeverityLevel  # noqa
from src.models.target_group import TargetGroup  # noqa
from src.models.tracked_account import TrackedAccount  # noqa
from src.models.trend_signal import TrendSignal  # noqa
from src.models.trope_entry import TropeDictionaryEntry  # noqa


async def init_db() -> None:
    """Create any missing tables directly from the models.

    Fast, and correct for a fresh database — but `create_all` never ALTERs an
    existing table, so it silently ignores added or changed columns. Anything with
    real data must go through `upgrade_db()` instead.

    Kept for tests, which build a throwaway database per run.
    """
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def upgrade_db() -> None:
    """Bring the database to the latest migration.

    The production path. Synchronous because Alembic drives its own event loop.
    """
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parent.parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(cfg, "head")


async def session_scope() -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on error.

    A bare async generator, which is what FastAPI's `Depends` requires — it drives the
    generator itself. Wrapping it in `@asynccontextmanager` (as this used to be) makes
    every endpoint raise "'_AsyncGeneratorContextManager' object is not an async
    iterator" the moment it touches the database.
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# `async with get_session() as session:` for ordinary code; `Depends(session_scope)`
# for FastAPI. Same generator, two call styles.
get_session = contextlib.asynccontextmanager(session_scope)

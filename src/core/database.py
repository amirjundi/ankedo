"""
Database initialization and session management.
"""
from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.base import Base, _get_engine, get_session_factory
# Import all models here so metadata is populated before create_all()
from src.models.agent_notification import AgentNotification  # noqa
from src.models.agent_worker_account import AgentWorkerAccount  # noqa
from src.models.case import Case  # noqa
from src.models.comment import Comment  # noqa
from src.models.evidence_package import EvidencePackage  # noqa
from src.models.gold_eval_entry import GoldEvalEntry  # noqa
from src.models.lexicon_entry import LexiconEntry  # noqa
from src.models.post import Post  # noqa
from src.models.queue_item import QueueItem  # noqa
from src.models.reviewer_decision import ReviewerDecision  # noqa
from src.models.tracked_account import TrackedAccount  # noqa
from src.models.trope_entry import TropeDictionaryEntry  # noqa


async def init_db() -> None:
    """Create all tables in the SQLite database if they don't exist."""
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@contextlib.asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Async context manager for yielding database sessions."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise

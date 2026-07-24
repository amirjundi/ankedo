"""AgentWorkerAccount — platform accounts the agent uses to crawl."""
from __future__ import annotations

import enum

from sqlalchemy import Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class WorkerStatus(str, enum.Enum):
    WARMUP = "Warmup"
    ACTIVE = "Active"
    CHALLENGED = "Challenged"
    BLOCKED = "Blocked"


class AgentWorkerAccount(Base):
    __tablename__ = "agent_worker_accounts"

    platform: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    proxy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fingerprint_seed: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(
        Enum(WorkerStatus), default=WorkerStatus.WARMUP, nullable=False
    )
    trust_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    last_used_at: Mapped[str | None] = mapped_column(String(50), nullable=True)

    def __repr__(self) -> str:
        return f"<AgentWorkerAccount platform={self.platform!r} user={self.username!r} status={self.status!r}>"

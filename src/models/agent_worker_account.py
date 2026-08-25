"""AgentWorkerAccount — platform accounts the agent uses to crawl."""
from __future__ import annotations

import enum

from sqlalchemy import Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class AccountStage(str, enum.Enum):
    """Where the account is in its lifecycle — how mature it is."""

    WARM_UP = "WarmUp"
    ACTIVE = "Active"
    RECOVERY = "Recovery"
    QUARANTINE = "Quarantine"


class AccountState(str, enum.Enum):
    """Whether the account is usable right now — orthogonal to stage."""

    HEALTHY = "Healthy"
    BLOCKED = "Blocked"


class AgentWorkerAccount(Base):
    __tablename__ = "agent_worker_accounts"

    platform: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_encrypted: Mapped[str] = mapped_column(String(1024), nullable=False)
    proxy: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fingerprint_seed: Mapped[str] = mapped_column(String(1024), nullable=False)
    stage: Mapped[str] = mapped_column(
        Enum(AccountStage), default=AccountStage.WARM_UP, nullable=False, index=True
    )
    state: Mapped[str] = mapped_column(
        Enum(AccountState), default=AccountState.HEALTHY, nullable=False, index=True
    )
    trust_score: Mapped[int] = mapped_column(Integer, default=100, nullable=False)  # 0-100
    last_used_at: Mapped[str | None] = mapped_column(String(50), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AgentWorkerAccount platform={self.platform!r} user={self.username!r} "
            f"stage={self.stage!r} state={self.state!r}>"
        )

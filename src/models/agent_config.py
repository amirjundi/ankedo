"""AgentConfig — runtime-tunable parameters the agent may adjust itself.

The split this table enforces: the agent tunes what costs *efficiency* when wrong, and
may only propose changes to what alters a *content decision*.

Bounds live in `settings.py` (human-set, file-based); the current value lives here. The
agent cannot move a value outside its bounds because `apply` clamps to them — the
guardrail is structural, not an instruction in a prompt, which is what FR-AG-7 requires.

Every change records who made it and why, and is revertible. "The agent changed
something and we do not know what" is not a recoverable state for a system producing
evidence about violence against real people.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class TunedBy(str, enum.Enum):
    HUMAN = "Human"
    AGENT = "Agent"
    DEFAULT = "Default"


class AgentConfig(Base):
    __tablename__ = "agent_config"

    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    min_value: Mapped[float] = mapped_column(Float, nullable=False)
    max_value: Mapped[float] = mapped_column(Float, nullable=False)
    default_value: Mapped[float] = mapped_column(Float, nullable=False)

    tuned_by: Mapped[str] = mapped_column(
        Enum(TunedBy), default=TunedBy.DEFAULT, nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Kept so `ankedo config revert` can undo the agent's last move without a human
    # having to know what the previous value was.
    previous_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:
        return f"<AgentConfig {self.key}={self.value} by={self.tuned_by}>"

"""GoldEvalEntry — holds the golden evaluation set for gating classifier changes."""
from __future__ import annotations

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class GoldEvalEntry(Base):
    __tablename__ = "gold_eval_entries"

    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    target_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dialect: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_hate_speech: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<GoldEvalEntry id={self.id!r} hate_speech={self.is_hate_speech!r}>"

"""LexiconEntry — fast first-pass lexicon lookup."""
from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class LexiconEntry(Base):
    __tablename__ = "lexicon_entries"

    term: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_group: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dialect: Mapped[str | None] = mapped_column(String(100), nullable=True)

    def __repr__(self) -> str:
        return f"<LexiconEntry id={self.id!r} term={self.term!r}>"

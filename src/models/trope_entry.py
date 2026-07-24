"""TropeDictionaryEntry — classification rules and tropes updated via the learning loop."""
from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class TropeDictionaryEntry(Base):
    __tablename__ = "trope_entries"

    surface_form: Mapped[str] = mapped_column(String(255), nullable=False)
    target_group: Mapped[str] = mapped_column(String(255), nullable=False)
    activation_condition: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(50), nullable=False)
    linked_case_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    def __repr__(self) -> str:
        return f"<TropeDictionaryEntry id={self.id!r} form={self.surface_form!r}>"

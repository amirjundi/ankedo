"""SeverityLevel — configurable severity scale.

A lookup table rather than an enum because the scale itself is an open domain
question (SRS §7 Q2): how many levels, and what drives escalation. Keeping it in
data means answering that later does not need a code change or a deploy.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class SeverityLevel(Base):
    __tablename__ = "severity_levels"

    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Whether reaching this level should escalate a case on its own.
    auto_escalate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    pack_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pack_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    def __repr__(self) -> str:
        return f"<SeverityLevel {self.ordinal}={self.label!r}>"

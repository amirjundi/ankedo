"""TargetGroup — the canonical taxonomy of monitored minority groups.

Group identity has to be canonical, not free text. A trope only fires when its
target group is matched in the context bundle (FR-CL-8), so "Yazidi", "yazidi" and
"الإيزيديون" arriving as three different strings means the trope silently never fires.
Content is expert-defined and ships in a knowledge pack (NFR-SF-2).
"""
from __future__ import annotations

from sqlalchemy import Boolean, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class TargetGroup(Base):
    __tablename__ = "target_groups"

    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)

    display_name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name_ar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name_ku: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Spellings, transliterations, Arabizi and both Kurdish scripts. Every missing
    # alias is a silent false negative.
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # How the group names itself — gates reclaimed-speech handling.
    self_reference_terms: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Groups commonly confused with this one, for per-group confusion analysis.
    adjacent_groups: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    pack_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pack_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    def __repr__(self) -> str:
        return f"<TargetGroup slug={self.slug!r}>"

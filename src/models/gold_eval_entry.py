"""GoldEvalEntry — the frozen evaluation set (FR-LE-3).

Every field here exists because the eval would be misleading without it.

`parent_post_text` is the critical one: the previous version stored only the comment,
which makes it impossible to test the behaviour the system is built around — the same
phrase being benign on one post and hateful on another. An eval set without context
can only measure keyword matching.

`label` is three-valued rather than a boolean. `ambiguous` is a real answer (FR-CL-11
routes genuine ambiguity to a human), and scoring a model wrong for being honestly
uncertain would train exactly the overconfidence this domain punishes.

`annotators` carries per-annotator labels so inter-rater agreement can be computed. If
κ is low the *definition* is broken, not the model, and no amount of tuning helps.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class GoldEvalEntry(Base):
    __tablename__ = "gold_eval_entries"

    # Stable id from the source file, so re-importing updates rather than duplicates.
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)

    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    # Without this the set cannot test context-dependence at all.
    parent_post_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    target_group: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    dialect: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    script: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # hate | benign | ambiguous
    label: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    severity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trope_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # [{"id": "a1", "label": "hate"}, ...] — >=2 entries feed the kappa calculation.
    annotators: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Marks the minimal pairs and genuinely difficult items, reported separately
    # because aggregate accuracy hides failure on exactly these.
    hard_case: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

    why: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    license: Mapped[str | None] = mapped_column(String(100), nullable=True)

    @property
    def is_hate_speech(self) -> bool:
        return self.label == "hate"

    def __repr__(self) -> str:
        return f"<GoldEvalEntry {self.external_id or self.id} label={self.label!r}>"

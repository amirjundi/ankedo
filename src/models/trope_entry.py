"""TropeDictionaryEntry — context-dependent implicatures.

A trope is a surface form that is benign alone but hateful when a target-group
activation condition holds (FR-CL-7). It fires ONLY when that condition is satisfied
by the context bundle (FR-CL-8); a bare surface-form match raises review priority and
must never auto-flag, or the system flags all devout speech.

This dictionary is the project's core differentiator (FR-LE-2a) — Iraqi coded speech
is in no model's training data.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Enum, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.associations import trope_target_groups
from src.models.base import Base
from src.models.lexicon_entry import TermScope


class TropeDictionaryEntry(Base):
    __tablename__ = "trope_entries"

    trope_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)

    # Many tropes are group-specific, but majority-framing implicatures — takfiri
    # accusation, impurity/contamination, "not really Iraqi" — get applied almost
    # identically across several minorities, so one trope can list several groups.
    scope: Mapped[str] = mapped_column(
        Enum(TermScope), default=TermScope.GROUP_SPECIFIC, nullable=False, index=True
    )

    # One row per trope; each surface form is {text, register}. Kept as a list rather
    # than a row per form so the implicature and its examples stay in one place.
    surface_forms: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # {requires_target_group, post_topic_any, negation_cancels}
    activation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    implicature: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # positive/negative pairs are the unit of data. A trope with no negative examples
    # cannot be trusted and is rejected by `ankedo pack verify`.
    positive_examples: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    negative_examples: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    counter_speech_examples: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    confirmed_in_cases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    pack_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    pack_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    target_groups: Mapped[list["TargetGroup"]] = relationship(  # noqa: F821
        secondary=trope_target_groups, lazy="selectin"
    )

    @property
    def group_slugs(self) -> list[str]:
        return [g.slug for g in self.target_groups]

    def activated_by(self, context_groups: set[str]) -> str | None:
        """Return the group slug that satisfies activation, or None.

        UNIVERSAL tropes need no group in context; group-specific ones fire only when
        one of their linked groups is present (FR-CL-8).
        """
        if self.scope == TermScope.UNIVERSAL:
            return "*"
        overlap = context_groups & set(self.group_slugs)
        return next(iter(overlap), None)

    def __repr__(self) -> str:
        return f"<TropeDictionaryEntry trope_id={self.trope_id!r} scope={self.scope!r}>"

"""LexiconEntry — fast first-pass lookup for explicit slurs."""
from __future__ import annotations

import enum

from sqlalchemy import Boolean, Enum, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.associations import lexicon_target_groups
from src.models.base import Base


class TermScope(str, enum.Enum):
    """Which groups a term is abuse toward.

    UNIVERSAL is deliberately explicit rather than "no groups linked", so that a
    mistakenly empty group list fails loudly instead of quietly applying a slur to
    every community at once.
    """

    GROUP_SPECIFIC = "GroupSpecific"  # abuse aimed at the linked group(s)
    UNIVERSAL = "Universal"           # applies regardless of who is targeted


class LexiconEntry(Base):
    """Local cache of the platform lexicon.

    The platform owns these terms (AGENT_CONTRACT.md, "Where the data lives") — one
    curator edit in the dashboard reaches every agent on the next sync, and a stolen
    or reimaged laptop leaks nothing that is not already on the server. Rows here are
    a cache keyed on `platform_id`, not a source of truth.
    """

    __tablename__ = "lexicon_entries"

    term: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # HateSpeechLexicon.id upstream. Null means locally proposed and not yet accepted.
    platform_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True, index=True)
    # slur | threat | dehumanization | incitement
    category: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(5), nullable=True, index=True)
    # Upstream regexes are matched case-insensitively. An entry that fails to compile
    # is skipped rather than aborting the scan.
    is_regex: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Verbatim upstream target_group string, kept even when it does not resolve to a
    # canonical group — an unresolved value is a real signal worth surfacing.
    raw_target_group: Mapped[str | None] = mapped_column(String(100), nullable=True)

    scope: Mapped[str] = mapped_column(
        Enum(TermScope), default=TermScope.GROUP_SPECIFIC, nullable=False, index=True
    )

    # JSON lists: a term can be attested in several dialects and scripts.
    dialect: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    script: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # FR-CL-4: explicit slurs flag regardless of the parent post's topic. Entries with
    # is_explicit=False only contribute when a target group is present in context —
    # they raise review priority, they never auto-flag on their own.
    is_explicit: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    severity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Obfuscations: spacing, letter swaps, emoji/leet substitution.
    variants: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Contexts that suppress the hit, e.g. news_quotation, academic, counter_speech.
    never_flag_when: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # Provenance is mandatory — entries trace to a confirmed incident, never to a
    # generic word list. `ankedo pack verify` rejects entries without it.
    source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    added_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    pack_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Fills the version Post.lexicon_version already expects to record (FR-CL-14).
    pack_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    target_groups: Mapped[list["TargetGroup"]] = relationship(  # noqa: F821
        secondary=lexicon_target_groups, lazy="selectin"
    )

    @property
    def group_slugs(self) -> list[str]:
        return [g.slug for g in self.target_groups]

    def applies_to(self, context_groups: set[str]) -> bool:
        """Whether this term is in scope given the groups present in context."""
        if self.scope == TermScope.UNIVERSAL:
            return True
        return bool(context_groups & set(self.group_slugs))

    def __repr__(self) -> str:
        return f"<LexiconEntry id={self.id!r} term={self.term!r} scope={self.scope!r}>"

"""EvidencePackage — generated when a reviewer confirms a flag."""
from __future__ import annotations

from sqlalchemy import ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class EvidencePackage(Base):
    __tablename__ = "evidence_packages"

    post_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("posts.id"), nullable=True, index=True
    )
    comment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("comments.id"), nullable=True, index=True
    )
    screenshot_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    html_snapshot_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    reviewer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    confirmed_at: Mapped[str] = mapped_column(String(50), nullable=False)
    trope_fired: Mapped[str | None] = mapped_column(String(255), nullable=True)
    multi_agent_trace_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Relationships
    post: Mapped["Post | None"] = relationship(  # noqa: F821
        back_populates="evidence_packages", foreign_keys=[post_id]
    )
    comment: Mapped["Comment | None"] = relationship(  # noqa: F821
        back_populates="evidence_packages", foreign_keys=[comment_id]
    )

    def __repr__(self) -> str:
        return f"<EvidencePackage id={self.id!r}>"

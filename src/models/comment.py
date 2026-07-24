"""Comment — always tied to a parent post."""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class Comment(Base):
    __tablename__ = "comments"

    post_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("posts.id"), nullable=False, index=True
    )
    platform_comment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_profile_picture: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Classification
    classification_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    hate_speech_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    multi_agent_trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    context_bundle_used: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Edge cases
    missing_parent_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    post: Mapped["Post"] = relationship(  # noqa: F821
        back_populates="comments", foreign_keys=[post_id]
    )
    evidence_packages: Mapped[list["EvidencePackage"]] = relationship(  # noqa: F821
        back_populates="comment", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Comment id={self.id!r} post_id={self.post_id!r}>"

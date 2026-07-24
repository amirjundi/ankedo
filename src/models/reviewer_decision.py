"""ReviewerDecision — labeled example with audit trail for learning loop."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class ReviewerDecision(Base):
    __tablename__ = "reviewer_decisions"

    post_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("posts.id"), nullable=True, index=True
    )
    comment_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("comments.id"), nullable=True, index=True
    )
    reviewer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Original classification state
    original_hate_speech_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    original_multi_agent_trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    
    # Reviewer's action
    is_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reviewer_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<ReviewerDecision id={self.id!r} confirmed={self.is_confirmed!r}>"

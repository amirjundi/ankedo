"""Post — the atomic unit of analysis collected from social media."""
from __future__ import annotations

import enum

from sqlalchemy import Enum, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base


class QueueState(str, enum.Enum):
    DISCOVERY = "Discovery"
    PROCESSING = "Processing"
    CLASSIFICATION = "Classification"
    REVIEW = "Review"
    DONE = "Done"
    REJECTED = "Rejected"


class Post(Base):
    __tablename__ = "posts"

    tracked_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tracked_accounts.id"), nullable=False, index=True
    )
    case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cases.id"), nullable=True, index=True
    )
    platform_post_id: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_media_urls: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_profile_picture: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    # Queue pipeline
    queue_state: Mapped[str] = mapped_column(
        Enum(QueueState), default=QueueState.DISCOVERY, nullable=False, index=True
    )
    queue_priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # higher = more urgent
    inflight_since: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Which group this post concerns (FR-CL-3). Case-supplied beats detected —
    # target_group_source records which signal produced it.
    target_group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("target_groups.id"), nullable=True, index=True
    )
    target_group_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dialect: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Verdict on images/video frames, not merely OCR'd text — memes are the dominant
    # vector and carry meaning no transcription preserves.
    media_classification: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Classification
    classification_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    hate_speech_flag: Mapped[bool | None] = mapped_column(nullable=True)
    multi_agent_trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    classification_model_versions: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    lexicon_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    trope_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Per-post statistics (updated atomically when all comments classified)
    comments_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comments_flagged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # OCR / media flags
    is_image_only: Mapped[bool] = mapped_column(default=False, nullable=False)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_failed: Mapped[bool] = mapped_column(default=False, nullable=False)

    # Deduplication
    collected_at: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Relationships
    tracked_account: Mapped["TrackedAccount"] = relationship(  # noqa: F821
        back_populates="posts", foreign_keys=[tracked_account_id]
    )
    comments: Mapped[list["Comment"]] = relationship(  # noqa: F821
        back_populates="post", lazy="select", cascade="all, delete-orphan"
    )
    evidence_packages: Mapped[list["EvidencePackage"]] = relationship(  # noqa: F821
        back_populates="post", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Post id={self.id!r} platform={self.platform!r} state={self.queue_state!r}>"

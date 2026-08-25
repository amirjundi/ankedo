"""OutboxItem — verdicts and scan logs waiting to reach the platform.

The agent runs on residential WiFi in Iraq. Submissions will fail: the line drops,
the VPS reboots, Cloudflare has a bad minute. Without somewhere to put a failed
submission, the classification work is simply lost — and re-collecting it means
re-scraping content that may since have been deleted, from accounts that may since
have been blocked.

So writes are queued here first and sent afterwards. `request_id` travels with the
item as an Idempotency-Key, which is what makes retrying safe: the retry loop cannot
distinguish "the server never saw it" from "the server processed it and the response
was lost", and on a bad connection the second genuinely happens.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class OutboxKind(str, enum.Enum):
    VERDICT = "Verdict"
    SCAN_LOG = "ScanLog"
    LEXICON_GAP = "LexiconGap"


class OutboxStatus(str, enum.Enum):
    PENDING = "Pending"
    SENT = "Sent"
    # Kept rather than deleted: a permanently failing item is evidence of a contract
    # mismatch, and silently dropping it would hide that the agent is losing work.
    FAILED = "Failed"


class OutboxItem(Base):
    __tablename__ = "outbox"

    kind: Mapped[str] = mapped_column(Enum(OutboxKind), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Stable across retries — this is the idempotency guarantee.
    request_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, default=lambda: str(uuid.uuid4())
    )

    status: Mapped[str] = mapped_column(
        Enum(OutboxStatus), default=OutboxStatus.PENDING, nullable=False, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<OutboxItem {self.kind} {self.status} attempts={self.attempts}>"

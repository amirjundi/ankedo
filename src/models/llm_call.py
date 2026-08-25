"""LLMCall — one row per model call, for cost control and audit.

Two jobs. Operationally it is the budget ledger: NGO funding is fixed and small, and a
vision loop on a viral thread can burn a month overnight, so spend has to be
attributable per case and per day. For audit it records which model and prompt
version produced a classification (FR-CL-14), which is what makes a verdict
reproducible when someone challenges it months later.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class LLMCall(Base):
    __tablename__ = "llm_calls"

    model: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # triage | specialist | critic | target_group | vision | chat
    purpose: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    prompt_version: Mapped[str | None] = mapped_column(String(50), nullable=True)

    case_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cases.id"), nullable=True, index=True
    )
    post_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Priced from configured per-token rates; 0 when rates are unset, since a wrong
    # hardcoded price is worse than an obviously absent one.
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<LLMCall {self.purpose} {self.model} tokens={self.total_tokens}>"

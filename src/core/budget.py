"""Token budget enforcement.

NFR-SC-2 requires expensive model calls to be gated. The concrete risk is not gradual
overspend — it is a single viral thread with thousands of comments, or a vision loop
that fails to terminate, consuming a fixed month's funding in an afternoon.

The guard is checked *before* each call and recorded after. It is a hard stop, not a
warning: FR-AG-7 lists rate and cost limits among the guardrails the agent may not
override, so nothing in the classification path can talk its way past this.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.settings import get_settings
from src.models.llm_call import LLMCall

log = structlog.get_logger()


class BudgetExceededError(RuntimeError):
    """Raised when a call would exceed a configured budget."""


async def tokens_used_today(session: AsyncSession) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    stmt = select(func.coalesce(func.sum(LLMCall.total_tokens), 0)).where(
        LLMCall.created_at >= since
    )
    return int((await session.execute(stmt)).scalar_one())


async def tokens_used_for_case(session: AsyncSession, case_id: str) -> int:
    stmt = select(func.coalesce(func.sum(LLMCall.total_tokens), 0)).where(
        LLMCall.case_id == case_id
    )
    return int((await session.execute(stmt)).scalar_one())


async def check_budget(session: AsyncSession, case_id: str | None = None) -> None:
    """Raise BudgetExceededError if the next call should not be made.

    Budgets of 0 mean unlimited, so an operator who has not configured spend limits
    is not blocked from running at all.
    """
    settings = get_settings()

    if settings.daily_token_budget:
        used = await tokens_used_today(session)
        if used >= settings.daily_token_budget:
            raise BudgetExceededError(
                f"daily token budget exhausted: {used}/{settings.daily_token_budget} "
                "in the last 24h"
            )

    if case_id and settings.per_case_token_budget:
        used = await tokens_used_for_case(session, case_id)
        if used >= settings.per_case_token_budget:
            raise BudgetExceededError(
                f"case {case_id} exhausted its budget: "
                f"{used}/{settings.per_case_token_budget}"
            )


async def record_call(
    session: AsyncSession,
    *,
    model: str,
    purpose: str,
    prompt_version: str | None = None,
    prompt_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
    case_id: str | None = None,
    post_id: str | None = None,
    succeeded: bool = True,
    error: str | None = None,
) -> LLMCall:
    """Write the ledger row. Failed calls are recorded too — they cost tokens."""
    settings = get_settings()
    total = prompt_tokens + output_tokens
    cost = (
        prompt_tokens * settings.input_token_cost_usd
        + output_tokens * settings.output_token_cost_usd
    )

    call = LLMCall(
        model=model,
        purpose=purpose,
        prompt_version=prompt_version,
        case_id=case_id,
        post_id=post_id,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        cost_usd=cost,
        latency_ms=latency_ms,
        succeeded=succeeded,
        error=error,
    )
    session.add(call)
    await session.flush()
    return call

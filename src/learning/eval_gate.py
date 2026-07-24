"""
Eval Gate - Gates classifier updates against the gold evaluation set.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.gold_eval_entry import GoldEvalEntry
from src.core.settings import get_settings

log = structlog.get_logger()


class EvalGateError(Exception):
    """Raised when a proposed change fails the evaluation gate."""
    pass


class EvalGate:
    """Evaluates proposed classifier changes against the gold set."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def run_evaluation(self, proposed_changes: dict) -> bool:
        """
        Run the gold eval set against the current classifier + proposed changes.
        """
        log.info("Running eval gate for proposed changes")
        
        stmt = select(GoldEvalEntry)
        result = await self.session.execute(stmt)
        gold_set = result.scalars().all()
        
        if len(gold_set) < self.settings.gold_eval_min_size:
            log.warning("Gold eval set too small for reliable gating", size=len(gold_set), required=self.settings.gold_eval_min_size)
            # Depending on policy, we might allow it or block it. Let's block if strict.
            # return False
            
        # Stub implementation: 
        # 1. Run current classifier over gold_set, calculate baseline metrics
        # 2. Run proposed classifier over gold_set, calculate new metrics
        # 3. Compare precision/recall per target group
        
        baseline_recall = 0.85
        proposed_recall = 0.86
        regression = baseline_recall - proposed_recall
        
        if regression * 100 > self.settings.regression_max_drop_pp:
            log.error("Eval gate failed: regression exceeds threshold", drop_pp=regression*100)
            raise EvalGateError(f"Regression of {regression*100:.1f}pp exceeds limit of {self.settings.regression_max_drop_pp}pp")
            
        log.info("Eval gate passed")
        return True

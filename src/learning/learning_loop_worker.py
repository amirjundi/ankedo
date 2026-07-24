"""
Learning Loop Worker - Automates the artifact generation and eval gating process.
"""
from __future__ import annotations

import asyncio
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.reviewer_decision import ReviewerDecision
from src.learning.artifact_generator import ArtifactGenerator
from src.learning.eval_gate import EvalGate

log = structlog.get_logger()


class LearningLoopWorker:
    """Background worker that continuously improves the classifier."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.generator = ArtifactGenerator(session)
        self.evaluator = EvalGate(session)

    async def run_cycle(self) -> None:
        """Process new decisions, generate artifacts, and gate them."""
        log.info("Starting learning loop cycle")
        
        # Get unprocessed decisions
        # In a real system, we'd have a processed_flag on ReviewerDecision
        stmt = select(ReviewerDecision).order_by(ReviewerDecision.created_at.desc()).limit(50)
        result = await self.session.execute(stmt)
        decisions = result.scalars().all()
        
        proposed_lexicon = []
        proposed_tropes = []
        
        for decision in decisions:
            lex, tropes = await self.generator.generate_from_decision(decision)
            proposed_lexicon.extend(lex)
            proposed_tropes.extend(tropes)
            
        if not proposed_lexicon and not proposed_tropes:
            log.info("No new artifacts generated in this cycle")
            return
            
        proposed_changes = {
            "lexicon": proposed_lexicon,
            "tropes": proposed_tropes
        }
        
        try:
            passed = await self.evaluator.run_evaluation(proposed_changes)
            if passed:
                log.info("Proposed changes passed eval gate, applying to production")
                for lex in proposed_lexicon:
                    self.session.add(lex)
                for trope in proposed_tropes:
                    self.session.add(trope)
                await self.session.commit()
        except Exception as e:
            log.error("Proposed changes failed eval gate", error=str(e))

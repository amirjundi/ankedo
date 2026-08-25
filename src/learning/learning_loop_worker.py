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
            result = await self.evaluator.run_evaluation(proposed_changes)
        except Exception as exc:
            log.error("Eval gate could not judge the proposal", error=str(exc))
            return

        if not result.passed:
            log.info(
                "Proposal rejected at the eval gate",
                summary=result.summary,
                groups=[r.group for r in result.regressions],
            )
            return

        # Passing the gate makes a proposal worth a curator's time. It does not make
        # it a detection rule.
        #
        # This used to session.add() and commit() here, which is the agent rewriting
        # the rules it is judged by — the thing FR-LE-1 exists to prevent — and it sat
        # behind a gate that always returned True. The gate is real now, but a gate
        # measures regression against a gold set; it cannot tell whether a term is
        # actually a slur, whether it is reclaimed in-community usage, or whether it
        # is a word for a community rather than a word against one. Only a curator
        # can, and the lexicon is human-authored by design: curators fill the workbook,
        # it imports to the platform, and the agent pulls it back down.
        #
        # ponytail: held, not submitted. The platform's lexicon-gaps endpoint is being
        # built in the other repo; until it exists there is nowhere to send these, and
        # holding them is the behaviour that cannot corrupt the dictionary. Wire the
        # submission here when the endpoint lands.
        log.info(
            "Proposal passed the eval gate — holding for curator review",
            summary=result.summary,
            lexicon=len(proposed_lexicon),
            tropes=len(proposed_tropes),
            note="not applied; awaiting the platform lexicon-gaps endpoint",
        )

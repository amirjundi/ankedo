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
from src.ettok.outbox import enqueue
from src.learning.eval_gate import EvalGate
from src.models.outbox import OutboxKind

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
        # So it goes to the platform as a proposal. Through the outbox rather than a
        # direct call: a dropped connection here must not lose the artifact, and the
        # drain already handles retries and the auth-failure stop.
        gaps = [self._as_gap(entry, result) for entry in proposed_lexicon]
        if gaps:
            await enqueue(self.session, OutboxKind.LEXICON_GAP, {"gaps": gaps})
            await self.session.commit()

        log.info(
            "Proposal queued for curator review",
            summary=result.summary,
            gaps=len(gaps),
            tropes_held=len(proposed_tropes),
        )

    @staticmethod
    def _as_gap(entry, result) -> dict:
        """Shape one proposed term for POST lexicon-gaps/.

        gate_effect carries the measured before/after. A curator seeing "lifts recall
        0.71 to 0.74" is deciding something different from a curator seeing a bare
        suggestion, and the numbers are the only part of this the agent is actually
        qualified to contribute.
        """
        return {
            "suggested_term": entry.term,
            "language": entry.language,
            "suggested_target_group": entry.raw_target_group,
            "suggested_category": entry.category,
            "rationale": entry.source or "generated from a reviewer decision",
            "gate_effect": {
                "recall_before": round(result.baseline.overall.recall, 3),
                "recall_after": round(result.proposed.overall.recall, 3),
            },
        }

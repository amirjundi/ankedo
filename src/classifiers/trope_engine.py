"""
Trope Engine - Evaluates context against learned hate speech tropes.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.trope_entry import TropeDictionaryEntry

log = structlog.get_logger()


class TropeEngine:
    """Matches text against complex trope activation conditions."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def evaluate(self, text: str, lexicon_hits: list[dict] = None) -> list[dict]:
        """
        Evaluate if any tropes are activated.
        This is a pre-filter before the LLM Linguistic Specialist.
        """
        if not text:
            return []
            
        # Stub implementation: In a real system, this might use a lightweight embedding
        # model or keyword heuristics to find candidate tropes, which the Specialist LLM
        # then verifies. For now, we return candidates that match exact surface forms.
        
        stmt = select(TropeDictionaryEntry)
        result = await self.session.execute(stmt)
        tropes = result.scalars().all()
        
        fired_tropes = []
        for trope in tropes:
            if trope.surface_form.lower() in text.lower():
                fired_tropes.append({
                    "id": trope.id,
                    "surface_form": trope.surface_form,
                    "target_group": trope.target_group,
                    "severity": trope.severity,
                    "activation_condition": trope.activation_condition
                })
                
        if fired_tropes:
            log.debug("Tropes activated", count=len(fired_tropes))
            
        return fired_tropes

"""
Critic Agent - Reviews the Specialist's conclusion for hallucinations and bias.
"""
from __future__ import annotations

import structlog
from src.core.settings import get_settings

log = structlog.get_logger()


class CriticAgent:
    """Independent reviewer of the Specialist's conclusion."""

    def __init__(self):
        self.settings = get_settings()
        self.model = self.settings.critic_model

    async def evaluate(self, text: str, specialist_decision: dict) -> dict:
        """
        Review the Specialist's conclusion.
        Returns a decision dict: {"agrees_with_specialist": bool, "rationale": str}
        """
        log.info("Critic agent evaluating", model=self.model)
        
        # Stub implementation
        # By default, agree with the specialist in this stub
        
        decision = {
            "agent": "CriticAgent",
            "model": self.model,
            "agrees_with_specialist": True,
            "rationale": "Stub critic review: reasoning is sound and no hallucination detected."
        }
        
        return decision

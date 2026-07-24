"""
Triage Agent - Fast LLM call to drop obvious non-hate speech.
"""
from __future__ import annotations

import structlog
from src.core.settings import get_settings

log = structlog.get_logger()


class TriageAgent:
    """Evaluates content quickly to determine if deeper analysis is needed."""

    def __init__(self):
        self.settings = get_settings()
        self.model = self.settings.triage_model

    async def evaluate(self, text: str, context: dict) -> dict:
        """
        Evaluate text.
        Returns a decision dict: {"requires_specialist": bool, "rationale": str}
        """
        log.info("Triage agent evaluating", model=self.model)
        # Stub implementation: In reality, this calls an LLM (e.g., via LangChain/litellm)
        # For now, we assume everything goes to the specialist unless it's empty
        
        requires_specialist = bool(text and len(text.strip()) > 0)
        
        decision = {
            "agent": "TriageAgent",
            "model": self.model,
            "requires_specialist": requires_specialist,
            "rationale": "Stub triage decision"
        }
        
        return decision

"""
LangGraph orchestrator for the multi-agent classification committee.
Connects Triage -> Specialist -> Critic.
"""
from __future__ import annotations

import structlog
from typing import TypedDict, Annotated, Sequence
import operator

from src.classifiers.committee.triage_agent import TriageAgent
from src.classifiers.committee.specialist_agent import SpecialistAgent
from src.classifiers.committee.critic_agent import CriticAgent

log = structlog.get_logger()

# In a real LangGraph implementation, we would define the state and nodes properly
class AgentState(TypedDict):
    text: str
    context: dict
    lexicon_hits: list[dict]
    tropes_fired: list[dict]
    triage_decision: dict | None
    specialist_decision: dict | None
    critic_decision: dict | None
    final_classification: dict | None
    committee_disagreement: bool


class CommitteeOrchestrator:
    """Manages the classification workflow between the three agents."""

    def __init__(self):
        self.triage = TriageAgent()
        self.specialist = SpecialistAgent()
        self.critic = CriticAgent()

    async def run(self, text: str, context: dict, lexicon_hits: list[dict], tropes_fired: list[dict]) -> dict:
        """
        Execute the committee graph.
        Returns the final classification result and the full trace.
        """
        log.info("Starting committee orchestrator")
        
        trace = {
            "lexicon_hits": lexicon_hits,
            "tropes_fired": tropes_fired,
            "triage": None,
            "specialist": None,
            "critic": None
        }

        # Node 1: Triage
        triage_decision = await self.triage.evaluate(text, context)
        trace["triage"] = triage_decision
        
        if not triage_decision["requires_specialist"]:
            log.info("Content dropped at Triage")
            return {
                "hate_speech_flag": False,
                "classification_score": 0.0,
                "committee_disagreement": False,
                "trace": trace
            }

        # Node 2: Specialist
        specialist_decision = await self.specialist.evaluate(text, context, lexicon_hits, tropes_fired)
        trace["specialist"] = specialist_decision

        # Node 3: Critic
        critic_decision = await self.critic.evaluate(text, specialist_decision)
        trace["critic"] = critic_decision
        
        committee_disagreement = not critic_decision["agrees_with_specialist"]
        
        if committee_disagreement:
            log.warning("Committee disagreement detected")

        # Final result
        # If there's disagreement, we might lower confidence or flag for mandatory review
        # For this stub, we trust the specialist unless critic disagrees strongly
        
        is_hate_speech = specialist_decision["is_hate_speech"]
        score = specialist_decision["hate_speech_score"]
        
        # Adjust score if critic disagrees (simulate ambiguity)
        if committee_disagreement and is_hate_speech:
            score = max(0.5, score - 0.2)
            
        return {
            "hate_speech_flag": is_hate_speech,
            "classification_score": score,
            "committee_disagreement": committee_disagreement,
            "trace": trace
        }

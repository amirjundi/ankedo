"""Triage — a cheap gate that drops obviously clear content.

Runs on the cheapest model, before the expensive specialist pass (NFR-SC-2). It is
deliberately biased toward passing items through: a wrong "no" here means the item is
never examined again, while a wrong "yes" only costs one more call.
"""
from __future__ import annotations

import structlog

from src.classifiers.committee.schemas import TriageDecision
from src.classifiers.context_bundle import ContextBundle
from src.classifiers.llm_client import LLMClient
from src.core.settings import get_settings

log = structlog.get_logger()

PROMPT_VERSION = "triage-v1"

SYSTEM = """You screen social media comments for a human rights organisation in Iraq \
that monitors hate speech against minority communities (Yazidi, Christian, Shabak, \
Kaka'i, Sabian-Mandaean, Turkmen, Faili Kurd, Baha'i).

Your only job is to decide whether a comment needs closer examination. You are not \
deciding whether it is hate speech.

Hostility here is frequently indirect. A comment can carry no slur at all and still \
be an attack, because of what it replies to — a religious phrase, a proverb, or an \
apparently innocuous remark can invoke a well-known libel when placed on content \
about a particular community.

Pass an item through whenever there is any plausible reading in which it is hostile \
toward the group the post concerns. Only stop items that are clearly unrelated or \
clearly harmless. When you are unsure, pass it through."""


class TriageAgent:
    """First pass: is deeper analysis warranted?"""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.model = get_settings().triage_model

    async def evaluate(self, bundle: ContextBundle) -> dict:
        if not (bundle.comment_text or "").strip():
            return {
                "agent": "TriageAgent",
                "model": self.model,
                "prompt_version": PROMPT_VERSION,
                "requires_specialist": False,
                "rationale": "empty text",
            }

        decision = await self.llm.generate(
            model=self.model,
            prompt=bundle.as_prompt_context(),
            schema=TriageDecision,
            purpose="triage",
            prompt_version=PROMPT_VERSION,
            system_instruction=SYSTEM,
            case_id=bundle.case_id,
        )

        return {
            "agent": "TriageAgent",
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "requires_specialist": decision.requires_specialist,
            "rationale": decision.rationale,
        }

"""Critic — an independent check on the specialist.

The old stub always agreed, which made `committee_disagreement` dead code and the
critic decorative. It must be able to actually disagree, and it is prompted to look
for the two failure directions that matter in this domain rather than to validate.

The asymmetry is deliberate. Over-flagging silences the community the system exists
to protect — flagging someone's ordinary prayer, or the activist refuting a libel, is
a direct harm done by us. Under-flagging lets an attack pass. The critic is asked to
watch for both, and disagreement routes the item to a human rather than resolving it
automatically.
"""
from __future__ import annotations

import structlog

from src.classifiers.committee.schemas import CriticDecision
from src.classifiers.context_bundle import ContextBundle
from src.classifiers.llm_client import LLMClient
from src.core.settings import get_settings

log = structlog.get_logger()

PROMPT_VERSION = "critic-v1"

SYSTEM = """You review another analyst's hate-speech classification for a human \
rights organisation in Iraq. Your job is to catch errors, not to confirm the \
conclusion. Agreeing when the analysis is sound is correct; agreeing reflexively is \
not useful.

Check specifically for:

FALSE POSITIVES — the more damaging direction, because they harm the community we \
protect:
- ordinary religious speech, proverbs or idioms read as hostile without the context \
that would make them so
- counter-speech: someone quoting a slur or libel in order to condemn or refute it
- reclaimed in-group usage, news reporting, or academic discussion
- a trope treated as fired when the post does not actually concern that group

FALSE NEGATIVES:
- indirect or coded hostility dismissed because no slur is present
- a known libel invoked through an apparently innocuous phrase
- hostility toward a group that the analyst failed to identify

ALSO CHECK:
- claims not supported by the parent post — a rationale that invents context
- a target group named that does not appear anywhere in the material
- confidence out of proportion to genuinely ambiguous evidence

If you disagree, say what was wrong and give your own verdict. Disagreement sends the \
item to a human reviewer, which is the correct outcome for a genuinely hard case."""


class CriticAgent:
    """Independent reviewer of the specialist's conclusion."""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.model = get_settings().critic_model

    async def evaluate(self, bundle: ContextBundle, specialist: dict) -> dict:
        prompt = (
            f"{bundle.as_prompt_context()}\n\n"
            "THE ANALYST'S CLASSIFICATION:\n"
            f"- verdict: {specialist['verdict']}\n"
            f"- confidence: {specialist['confidence']}\n"
            f"- category: {specialist['category']}\n"
            f"- target group: {specialist.get('target_group')}\n"
            f"- severity: {specialist['severity']}\n"
            f"- relies on context: {specialist['relies_on_context']}\n"
            f"- rationale: {specialist['rationale']}\n"
        )

        decision = await self.llm.generate(
            model=self.model,
            prompt=prompt,
            schema=CriticDecision,
            purpose="critic",
            prompt_version=PROMPT_VERSION,
            system_instruction=SYSTEM,
            case_id=bundle.case_id,
        )

        return {
            "agent": "CriticAgent",
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "agrees_with_specialist": decision.agrees_with_specialist,
            "concern": decision.concern,
            "suggested_verdict": (
                decision.suggested_verdict.value if decision.suggested_verdict else None
            ),
            "rationale": decision.rationale,
        }

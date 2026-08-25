"""Linguistic specialist — the actual judgement.

The prompt asks the **relational** question FR-CL-2 specifies:

    "Given a post concerning group X, does this comment express hostility toward X?"

and never "is this text hateful?". That distinction is the whole design. A comment
reading `اعوذ بالله من الشيطان الرجيم` is ordinary piety in isolation; on Yazidi content
it invokes the devil-worship libel. A prompt that asks about the text alone cannot
reach the right answer in either direction.

Fired tropes and lexicon hits are supplied as evidence, not as verdicts. The
deterministic layer already checked activation conditions (FR-CL-8); the model weighs
that evidence and may still disagree with it.
"""
from __future__ import annotations

import json

import structlog

from src.classifiers.committee.schemas import SpecialistDecision
from src.classifiers.context_bundle import ContextBundle
from src.classifiers.llm_client import LLMClient
from src.core.settings import get_settings

log = structlog.get_logger()

PROMPT_VERSION = "specialist-v1"

SYSTEM = """You analyse Arabic (Modern Standard and Iraqi dialect) and Kurdish \
(Sorani and Kurmanji) social media comments for a human rights organisation \
monitoring hate speech against minority communities in Iraq.

THE QUESTION YOU ANSWER

Given a post concerning group X, does this comment express hostility, dehumanization, \
or a known hateful trope toward X — including through indirect, coded, religious, or \
otherwise implicit language?

You are NOT answering "is this text offensive in isolation". The same words can be \
ordinary speech on one post and an attack on another. Judge the comment in relation \
to the post it replies to.

HOW TO WEIGH CONTEXT

- If the post concerns no minority group, a comment carrying only context-dependent \
signals is NOT hate speech. Explicit slurs remain hate speech regardless of topic.
- Religious formulas, proverbs and idioms are ordinary speech by default. They become \
hateful only when directed at a community in a way that invokes a known libel.
- Quoting hate in order to condemn it is counter-speech, not hate speech. People \
defending a community must never be classified as attacking it.
- Reporting, academic discussion and reclaimed in-group usage are not hate speech.

WHEN YOU ARE UNSURE

Answer "ambiguous". Coded speech can be sincere, and a human reviewer will decide. \
A confident wrong answer is far more damaging than an honest uncertain one: \
over-flagging silences the very community this system protects, and under-flagging \
lets an attack pass as piety.

Write the rationale by reference to the parent post, in English, for an analyst who \
may not read Arabic or Kurdish."""


class SpecialistAgent:
    """Deep cultural and linguistic analysis of the context bundle."""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        self.model = get_settings().specialist_model

    async def evaluate(
        self,
        bundle: ContextBundle,
        lexicon_hits: list[dict],
        tropes_fired: list[dict],
        trope_candidates: list[dict] | None = None,
    ) -> dict:
        evidence = _format_evidence(lexicon_hits, tropes_fired, trope_candidates or [])
        prompt = f"{bundle.as_prompt_context()}\n\n{evidence}"

        decision = await self.llm.generate(
            model=self.model,
            prompt=prompt,
            schema=SpecialistDecision,
            purpose="specialist",
            prompt_version=PROMPT_VERSION,
            system_instruction=SYSTEM,
            case_id=bundle.case_id,
        )

        return {
            "agent": "SpecialistAgent",
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "verdict": decision.verdict.value,
            "is_hate_speech": decision.verdict.value == "hate",
            "hate_speech_score": decision.confidence
            if decision.verdict.value == "hate"
            else 1.0 - decision.confidence,
            "confidence": decision.confidence,
            "category": decision.category.value,
            "target_group": decision.target_group,
            "severity": decision.severity,
            "relies_on_context": decision.relies_on_context,
            "rationale": decision.rationale,
        }


def _format_evidence(
    lexicon_hits: list[dict], tropes_fired: list[dict], candidates: list[dict]
) -> str:
    """Present deterministic-layer findings as evidence the model may weigh."""
    lines = ["DETERMINISTIC EVIDENCE (findings from the curated dictionaries):"]

    if tropes_fired:
        lines.append("\nTropes whose activation conditions were satisfied:")
        for trope in tropes_fired:
            lines.append(
                f"- {trope['trope_id']}: matched {trope['surface_form']!r}. "
                f"{trope.get('implicature') or ''} ({trope.get('reason')})"
            )
    if candidates:
        lines.append(
            "\nTrope surface forms present but NOT activated — the phrase appears, "
            "but its condition was not met. Treat as weak signal only:"
        )
        for candidate in candidates:
            lines.append(
                f"- {candidate['trope_id']}: {candidate['surface_form']!r} "
                f"({candidate.get('reason')})"
            )
    if lexicon_hits:
        lines.append("\nLexicon terms matched:")
        for hit in lexicon_hits:
            scope = "explicit slur" if hit.get("is_explicit") else "context-dependent"
            in_scope = "" if hit.get("in_scope", True) else " [not in scope for this post]"
            lines.append(f"- {hit.get('term')!r} ({scope}{in_scope})")

    if len(lines) == 1:
        lines.append("None. Absence of a dictionary match does not mean the comment is benign.")

    lines.append(
        "\nThese are findings from curated dictionaries, not verdicts. Weigh them; "
        "disagree if the context warrants."
    )
    return "\n".join(lines)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False)

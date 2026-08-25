"""Committee orchestration: deterministic layer, then triage → specialist → critic.

SRS §1.1 splits the system deliberately: agentic at the orchestration layer,
deterministic at the classification layer. So the lexicon and trope engine run first
and their findings are handed to the model as evidence — the activation conditions
(FR-CL-8) are enforced in code, not left to the model to remember.

Three sequential awaits. No graph framework: `langgraph` was a declared dependency
that nothing imported, and it was dropped rather than adopted.
"""
from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.committee.critic_agent import CriticAgent
from src.classifiers.committee.specialist_agent import SpecialistAgent
from src.classifiers.committee.triage_agent import TriageAgent
from src.classifiers.context_bundle import ContextBundle
from src.classifiers.lexicon import LexiconMatcher
from src.classifiers.llm_client import LLMClient
from src.classifiers.trope_engine import TropeEngine
from src.core.settings import get_settings

log = structlog.get_logger()


class CommitteeOrchestrator:
    """Runs the full classification pipeline over a context bundle."""

    def __init__(self, session: AsyncSession, llm: LLMClient | None = None):
        self.session = session
        self.settings = get_settings()
        self.llm = llm or LLMClient(session)
        # Fitted by `ankedo eval calibrate`; 1.0 until then, which leaves raw scores
        # unchanged rather than inventing a correction.
        self._temperature: float | None = None
        self.lexicon = LexiconMatcher(session)
        self.tropes = TropeEngine(session)
        self.triage = TriageAgent(self.llm)
        self.specialist = SpecialistAgent(self.llm)
        self.critic = CriticAgent(self.llm)

    async def _calibrate(self, confidence: float) -> float:
        from src.learning.calibration import apply_temperature, current_temperature

        if self._temperature is None:
            self._temperature = await current_temperature(self.session)
        return apply_temperature(confidence, self._temperature)

    async def run(self, bundle: ContextBundle) -> dict:
        context_groups = set(bundle.target_groups or [])

        # --- deterministic layer (FR-CL-8 enforced here, in code) -------------
        lexicon_hits = await self.lexicon.scan_text(bundle.comment_text, context_groups)
        trope_result = await self.tropes.evaluate(bundle.to_dict())
        fired, candidates = trope_result["fired"], trope_result["candidates"]

        trace = {
            "bundle": bundle.to_dict(),
            "lexicon_hits": lexicon_hits,
            "tropes_fired": fired,
            "trope_candidates": candidates,
            "triage": None,
            "specialist": None,
            "critic": None,
        }

        # --- triage -----------------------------------------------------------
        triage = await self.triage.evaluate(bundle)
        trace["triage"] = triage

        if not triage["requires_specialist"]:
            # An explicit slur must never be dropped at triage, regardless of topic
            # (FR-CL-4). Triage runs on the cheapest model; the dictionary overrides it.
            explicit = [h for h in lexicon_hits if h.get("is_explicit")]

            # A fired or candidate trope overrides the drop too. The deterministic
            # layer above already did the work of matching this comment against the
            # trope dictionary, and the result was computed and then ignored unless a
            # lexicon term happened to hit as well.
            #
            # That gap is the whole point of the trope layer. Mockery — the form most
            # reported in the Duhok survey — rarely contains a term any dictionary
            # holds; the pattern is what identifies it. So the items tropes exist to
            # catch were exactly the items triage could discard unchallenged, and a
            # cheap model's "not worth looking at" was final for them.
            #
            # Candidates count, not only fired patterns: a candidate is a pattern that
            # matched but whose activation topic was not confirmed, which is a question
            # for the specialist rather than an answer. Escalating costs one specialist
            # call; dropping loses the item silently, and the operator's standing
            # instruction is that a miss is worse than an over-flag.
            if not explicit and not fired and not candidates:
                log.info("Dropped at triage")
                return _result("benign", 0.0, False, trace, severity=0)

            log.info(
                "Triage overridden",
                explicit_terms=len(explicit),
                tropes_fired=len(fired),
                trope_candidates=len(candidates),
            )

        # --- specialist -------------------------------------------------------
        specialist = await self.specialist.evaluate(bundle, lexicon_hits, fired, candidates)
        trace["specialist"] = specialist

        # --- critic -----------------------------------------------------------
        critic = await self.critic.evaluate(bundle, specialist)
        trace["critic"] = critic

        verdict = specialist["verdict"]
        # Calibrate before any threshold is applied. auto_flag_threshold decides what
        # a human never sees, so it has to be compared against a number that means
        # what it says (FR-CL-10).
        raw_confidence = specialist["confidence"]
        confidence = await self._calibrate(raw_confidence)
        trace["calibration"] = {"raw": raw_confidence, "temperature": self._temperature}
        disagreement = not critic["agrees_with_specialist"]

        if disagreement:
            # Never silently resolve a disagreement. Lower confidence so the item
            # lands in the review band and a human decides (FR-CL-11).
            log.warning(
                "Committee disagreement",
                specialist=verdict,
                critic=critic.get("suggested_verdict"),
            )
            confidence = min(confidence, self.settings.borderline_high)

        return _result(
            verdict,
            confidence,
            disagreement,
            trace,
            severity=specialist["severity"],
            category=specialist["category"],
            target_group=specialist.get("target_group"),
            relies_on_context=specialist["relies_on_context"],
        )


def _result(
    verdict: str,
    confidence: float,
    disagreement: bool,
    trace: dict,
    *,
    severity: int = 0,
    category: str = "none",
    target_group: str | None = None,
    relies_on_context: bool = False,
) -> dict:
    return {
        "verdict": verdict,
        "hate_speech_flag": verdict == "hate",
        "classification_score": confidence if verdict == "hate" else 0.0,
        "confidence": confidence,
        "severity": severity,
        "category": category,
        "target_group": target_group,
        "relies_on_context": relies_on_context,
        "committee_disagreement": disagreement,
        "trace": trace,
    }

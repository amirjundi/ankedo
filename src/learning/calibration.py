"""Confidence calibration.

FR-CL-10 requires a *calibrated* confidence. A raw LLM score is not one: models are
systematically overconfident, so "0.9" typically means the model is right well under
90% of the time. Everything downstream keys on that number — `auto_flag_threshold`
decides what a human never sees, and the borderline band decides what reaches a
reviewer at all — so an uncalibrated score makes those thresholds mean something other
than what they appear to.

Temperature scaling: fit one scalar T on the gold set and divide the logit by it.
Chosen over anything richer because it cannot change the *ranking* of items, only how
the scores spread. Ranking is what the eval measures; a method that reorders items
would improve calibration while quietly degrading the classifier.

`ankedo eval calibrate` fits it. Until then the raw score is used unchanged, with the
uncertainty visible rather than hidden behind a fabricated default.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.agent_config import AgentConfig, TunedBy
from src.models.gold_eval_entry import GoldEvalEntry

log = structlog.get_logger()

CONFIG_KEY = "confidence_temperature"
_EPS = 1e-6


def _logit(p: float) -> float:
    p = min(max(p, _EPS), 1 - _EPS)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def apply_temperature(confidence: float, temperature: float) -> float:
    """Rescale a confidence. T > 1 softens, T < 1 sharpens, T == 1 is a no-op.

    The result never reaches exactly 0 or 1. Sharpening an already-extreme score
    saturates in floating point, and a confidence of exactly 1.0 asserts certainty —
    which is never true of a classifier, and makes any later logit infinite.
    """
    if temperature <= 0 or temperature == 1.0:
        return confidence
    scaled = _sigmoid(_logit(confidence) / temperature)
    return min(max(scaled, _EPS), 1 - _EPS)


@dataclass
class CalibrationReport:
    temperature: float
    samples: int
    ece_before: float
    ece_after: float
    bins: list[dict]

    @property
    def improved(self) -> bool:
        return self.ece_after < self.ece_before

    def summary(self) -> str:
        direction = "overconfident" if self.temperature > 1 else "underconfident"
        return (
            f"T={self.temperature:.2f} (model was {direction}), "
            f"ECE {self.ece_before:.3f} → {self.ece_after:.3f} over {self.samples} items"
        )


def expected_calibration_error(pairs: list[tuple[float, bool]], bins: int = 10) -> tuple[float, list[dict]]:
    """Mean gap between claimed confidence and observed accuracy.

    The number that answers "when this system says 0.9, how often is it right?" — which
    is the only question that makes a threshold meaningful.
    """
    buckets: list[list[tuple[float, bool]]] = [[] for _ in range(bins)]
    for confidence, correct in pairs:
        index = min(int(confidence * bins), bins - 1)
        buckets[index].append((confidence, correct))

    total = len(pairs)
    error = 0.0
    detail = []
    for index, bucket in enumerate(buckets):
        if not bucket:
            continue
        mean_confidence = sum(c for c, _ in bucket) / len(bucket)
        accuracy = sum(1 for _, ok in bucket if ok) / len(bucket)
        error += (len(bucket) / total) * abs(mean_confidence - accuracy)
        detail.append(
            {
                "range": f"{index / bins:.1f}-{(index + 1) / bins:.1f}",
                "count": len(bucket),
                "claimed": round(mean_confidence, 3),
                "actual": round(accuracy, 3),
            }
        )
    return error, detail


def fit_temperature(pairs: list[tuple[float, bool]]) -> float:
    """Find the T minimising negative log likelihood.

    A coarse-to-fine scan rather than gradient descent: one parameter over a bounded
    range, so a search is simpler to reason about and cannot diverge.
    """
    if len(pairs) < 10:
        return 1.0

    def nll(temperature: float) -> float:
        total = 0.0
        for confidence, correct in pairs:
            p = min(max(apply_temperature(confidence, temperature), _EPS), 1 - _EPS)
            total -= math.log(p) if correct else math.log(1 - p)
        return total

    best, best_loss = 1.0, nll(1.0)
    for step in (0.1, 0.01):
        low, high = max(0.05, best - step * 10), best + step * 10
        candidate = low
        while candidate <= high:
            loss = nll(candidate)
            if loss < best_loss:
                best, best_loss = candidate, loss
            candidate += step
    return round(best, 2)


async def calibrate(session: AsyncSession, results: list[tuple[GoldEvalEntry, dict]]) -> CalibrationReport:
    """Fit and store a temperature from scored gold items."""
    pairs: list[tuple[float, bool]] = []
    for entry, result in results:
        # Ambiguous gold items have no single right answer, so they cannot inform
        # whether a confidence was justified.
        if entry.label == "ambiguous" or result["verdict"] == "ambiguous":
            continue
        pairs.append((float(result["confidence"]), result["verdict"] == entry.label))

    if len(pairs) < 10:
        log.warning("Too few decisive items to calibrate", samples=len(pairs))
        return CalibrationReport(1.0, len(pairs), 0.0, 0.0, [])

    temperature = fit_temperature(pairs)
    before, _ = expected_calibration_error(pairs)
    after, bins = expected_calibration_error(
        [(apply_temperature(c, temperature), ok) for c, ok in pairs]
    )

    row = (
        await session.execute(select(AgentConfig).where(AgentConfig.key == CONFIG_KEY))
    ).scalar_one_or_none()
    if row is None:
        row = AgentConfig(
            key=CONFIG_KEY, value=1.0, min_value=0.05, max_value=10.0, default_value=1.0
        )
        session.add(row)
    row.previous_value = row.value
    row.value = temperature
    row.tuned_by = TunedBy.HUMAN  # produced by an eval run, not by the agent itself
    row.reason = f"fitted on {len(pairs)} gold items"
    await session.commit()

    report = CalibrationReport(temperature, len(pairs), before, after, bins)
    log.info("Calibration fitted", detail=report.summary())
    return report


async def current_temperature(session: AsyncSession) -> float:
    row = (
        await session.execute(select(AgentConfig).where(AgentConfig.key == CONFIG_KEY))
    ).scalar_one_or_none()
    return row.value if row else 1.0

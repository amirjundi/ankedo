"""Gates proposed classifier changes against the gold evaluation set.

This used to be a stub: it hardcoded baseline_recall = 0.85 and proposed_recall =
0.86, never ran the classifier, and therefore passed everything. It sat in front of
a path that wrote lexicon and trope entries straight to the database, so the only
thing standing between a generated artifact and the live detection rules always
said yes.

It now measures. Baseline is the gold set scored against the current rules; proposed
is the same set scored again with the artifacts applied inside a savepoint that is
always rolled back, so measuring a change never becomes applying it.

Two properties worth stating, because both cost something:

**It is expensive.** Two full passes over the gold set, each classifying every entry
through the committee. That is the price of a gate that can actually fail; the stub
was cheap because it did nothing.

**It judges on recall, per group.** The operator's call is that missing hate speech
is worse than over-flagging, so a proposal that lifts overall recall while dropping
it for one community is a regression, not a win — the aggregate hides exactly the
failure this system exists to avoid. Precision is measured and reported, never gated
on.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.settings import get_settings
from src.learning.evaluator import EvalReport, run_eval
from src.models.gold_eval_entry import GoldEvalEntry

log = structlog.get_logger()


class EvalGateError(Exception):
    """The gate could not reach a verdict.

    Distinct from a failed verdict: a caller must never read this as "passed". Raised
    when the gold set is too small to gate on, or when the evaluation itself broke.
    """


@dataclass
class GroupDrop:
    group: str
    baseline_recall: float
    proposed_recall: float

    @property
    def drop_pp(self) -> float:
        return (self.baseline_recall - self.proposed_recall) * 100


@dataclass
class GateResult:
    passed: bool
    baseline: EvalReport
    proposed: EvalReport
    regressions: list[GroupDrop] = field(default_factory=list)

    @property
    def summary(self) -> str:
        delta = (self.proposed.overall.recall - self.baseline.overall.recall) * 100
        head = f"recall {self.baseline.overall.recall:.3f} → {self.proposed.overall.recall:.3f} ({delta:+.1f}pp)"
        if not self.regressions:
            return head
        worst = max(self.regressions, key=lambda r: r.drop_pp)
        return f"{head}; worst group {worst.group} −{worst.drop_pp:.1f}pp"


class EvalGate:
    """Evaluates proposed classifier changes against the gold set."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def run_evaluation(self, proposed_changes: dict) -> GateResult:
        """Score the gold set with and without the proposal.

        Raises EvalGateError if no trustworthy verdict is possible.
        """
        count = len(
            (await self.session.execute(select(GoldEvalEntry))).scalars().all()
        )
        # Previously a warning with the `return False` commented out, so a gold set of
        # three entries produced a confident pass. Too small to judge is not a pass.
        if count < self.settings.gold_eval_min_size:
            raise EvalGateError(
                f"gold set has {count} entries, need {self.settings.gold_eval_min_size} "
                "to gate on — run `ankedo eval load` first"
            )

        log.info("Eval gate: scoring baseline", gold_entries=count)
        baseline = await run_eval(self.session)
        if baseline.errors:
            raise EvalGateError(
                f"baseline evaluation failed on {len(baseline.errors)} entries: "
                f"{baseline.errors[0]}"
            )

        log.info("Eval gate: scoring proposal")
        proposed = await self._score_with(proposed_changes)
        if proposed.errors:
            raise EvalGateError(
                f"proposed evaluation failed on {len(proposed.errors)} entries: "
                f"{proposed.errors[0]}"
            )

        regressions = self._regressions(baseline, proposed)
        result = GateResult(
            passed=not regressions,
            baseline=baseline,
            proposed=proposed,
            regressions=regressions,
        )

        if result.passed:
            log.info("Eval gate passed", summary=result.summary)
        else:
            log.error(
                "Eval gate failed",
                summary=result.summary,
                groups=[r.group for r in regressions],
            )
        return result

    async def _score_with(self, proposed_changes: dict) -> EvalReport:
        """Evaluate with the proposal applied, then undo it.

        The savepoint is what keeps this a measurement. Without the rollback, running
        the gate would install the very changes it is deciding about — which is how
        the old path behaved once the gate said yes.
        """
        artifacts = [
            *proposed_changes.get("lexicon", []),
            *proposed_changes.get("tropes", []),
        ]
        if not artifacts:
            raise EvalGateError("nothing proposed — no lexicon or trope artifacts")

        savepoint = await self.session.begin_nested()
        try:
            for artifact in artifacts:
                self.session.add(artifact)
            await self.session.flush()
            return await run_eval(self.session)
        finally:
            await savepoint.rollback()

    def _regressions(self, baseline: EvalReport, proposed: EvalReport) -> list[GroupDrop]:
        """Per-group recall drops beyond the allowed threshold.

        Groups absent from the baseline are skipped rather than treated as a drop from
        zero: a group with no positives in the gold set has no recall to regress, and
        counting it would block every proposal until the gold set covered all nine.
        """
        limit = self.settings.regression_max_drop_pp
        drops: list[GroupDrop] = []

        for group, before in baseline.by_group.items():
            if before.tp + before.fn == 0:
                continue
            after = proposed.by_group.get(group)
            if after is None:
                continue
            drop = GroupDrop(group, before.recall, after.recall)
            if drop.drop_pp > limit:
                drops.append(drop)

        overall = GroupDrop(
            "overall", baseline.overall.recall, proposed.overall.recall
        )
        if overall.drop_pp > limit:
            drops.append(overall)

        return drops

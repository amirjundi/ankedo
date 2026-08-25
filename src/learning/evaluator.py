"""Run the classifier over the gold set and report per-group metrics.

Accuracy is the wrong headline number here. Hate speech is a small fraction of
traffic, so a classifier that answers "benign" every time scores well and is useless.
Precision and recall, reported **per target group** (NFR-SF-3), are what matter — an
aggregate figure hides the group the system is failing, which is precisely the harm
this project exists to prevent.

Two extra breakdowns earn their place:

* **hard cases** — the minimal pairs, where the same text must resolve differently by
  context. Overall numbers can look healthy while every hard case is wrong, and those
  are the only ones that test the actual mechanism.
* **counter-speech** — items where someone quotes a libel to refute it. Flagging a
  community's defenders is the most damaging false positive this system can produce.

`ambiguous` gold items are scored separately rather than counted as errors: FR-CL-11
routes genuine ambiguity to a human, and punishing honest uncertainty trains the
overconfidence this domain punishes hardest.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.committee.orchestrator import CommitteeOrchestrator
from src.classifiers.context_bundle import ContextBundle
from src.classifiers.group_resolver import GroupResolver
from src.models.gold_eval_entry import GoldEvalEntry

log = structlog.get_logger()


@dataclass
class Metrics:
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    abstained: int = 0  # model said ambiguous on a decisive gold item

    @property
    def total(self) -> int:
        return self.tp + self.fp + self.tn + self.fn + self.abstained

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class EvalReport:
    overall: Metrics = field(default_factory=Metrics)
    by_group: dict[str, Metrics] = field(default_factory=lambda: defaultdict(Metrics))
    by_dialect: dict[str, Metrics] = field(default_factory=lambda: defaultdict(Metrics))
    hard_cases: Metrics = field(default_factory=Metrics)
    counter_speech: Metrics = field(default_factory=Metrics)
    failures: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def meets(self, min_precision: float, min_recall: float) -> bool:
        """Every group must clear the bar, not the average.

        An average lets strong performance on one community mask failure on another.
        """
        if self.overall.precision < min_precision or self.overall.recall < min_recall:
            return False
        return all(
            m.precision >= min_precision and m.recall >= min_recall
            for m in self.by_group.values()
            if m.tp + m.fn > 0  # groups with no positives cannot have recall
        )


async def run_eval(
    session: AsyncSession,
    *,
    limit: int | None = None,
    hard_only: bool = False,
) -> EvalReport:
    """Classify every gold entry and score the results."""
    stmt = select(GoldEvalEntry)
    if hard_only:
        stmt = stmt.where(GoldEvalEntry.hard_case.is_(True))
    if limit:
        stmt = stmt.limit(limit)

    entries = (await session.execute(stmt)).scalars().all()
    report = EvalReport()
    if not entries:
        report.errors.append("gold set is empty — run `ankedo eval load` first")
        return report

    orchestrator = CommitteeOrchestrator(session)
    resolver = GroupResolver(session)

    for entry in entries:
        groups = [entry.target_group] if entry.target_group else []
        if not groups and entry.parent_post_text:
            groups = await resolver.resolve_all(entry.parent_post_text)

        bundle = ContextBundle(
            comment_text=entry.text_content,
            parent_post_text=entry.parent_post_text or "",
            target_groups=groups,
            dialect=entry.dialect,
        )

        try:
            result = await orchestrator.run(bundle)
        except Exception as exc:
            report.errors.append(f"{entry.external_id or entry.id}: {exc}")
            continue

        _score(report, entry, result)

    log.info(
        "Eval complete",
        items=len(entries),
        precision=round(report.overall.precision, 3),
        recall=round(report.overall.recall, 3),
    )
    return report


def _score(report: EvalReport, entry: GoldEvalEntry, result: dict) -> None:
    predicted, actual = result["verdict"], entry.label
    buckets = [report.overall]
    if entry.target_group:
        buckets.append(report.by_group[entry.target_group])
    if entry.dialect:
        buckets.append(report.by_dialect[entry.dialect])
    if entry.hard_case:
        buckets.append(report.hard_cases)
    if entry.category == "counter_speech":
        buckets.append(report.counter_speech)

    if actual == "ambiguous":
        # Not scored as right or wrong — the correct behaviour is to route to a human.
        outcome = "abstained"
    elif predicted == "ambiguous":
        outcome = "abstained"
    elif predicted == "hate" and actual == "hate":
        outcome = "tp"
    elif predicted == "hate" and actual == "benign":
        outcome = "fp"
    elif predicted == "benign" and actual == "benign":
        outcome = "tn"
    else:
        outcome = "fn"

    for bucket in buckets:
        setattr(bucket, outcome, getattr(bucket, outcome) + 1)

    if outcome in ("fp", "fn"):
        report.failures.append(
            {
                "id": entry.external_id or entry.id,
                "expected": actual,
                "got": predicted,
                "kind": outcome,
                "hard_case": entry.hard_case,
                "target_group": entry.target_group,
                "text": entry.text_content[:120],
                "parent_post": (entry.parent_post_text or "")[:120],
                "why_expected": entry.why,
                "model_rationale": (result["trace"].get("specialist") or {}).get("rationale"),
            }
        )


def cohens_kappa(entries: list[GoldEvalEntry]) -> tuple[float | None, int]:
    """Agreement between the first two annotators on doubly-labelled items.

    Below ~0.6 the labelling guidance is the problem, not the model — more data will
    not fix a definition annotators cannot apply consistently.
    """
    pairs = [
        (e.annotators[0].get("label"), e.annotators[1].get("label"))
        for e in entries
        if len(e.annotators or []) >= 2
    ]
    if len(pairs) < 2:
        return None, len(pairs)

    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n

    labels = {label for pair in pairs for label in pair}
    expected = sum(
        (sum(1 for a, _ in pairs if a == label) / n)
        * (sum(1 for _, b in pairs if b == label) / n)
        for label in labels
    )
    if expected == 1:
        return 1.0, n
    return (observed - expected) / (1 - expected), n

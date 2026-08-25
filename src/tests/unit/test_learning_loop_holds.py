"""The learning loop must not write its own detection rules.

The lexicon is human-authored: curators fill the workbook, it imports to the
platform, the agent pulls it back. The worker used to session.add() and commit()
generated artifacts straight into the live rules, behind a gate that always
returned True — the agent editing the dictionary it is judged by (FR-LE-1).

It now proposes instead: a passing artifact is queued for the platform's
lexicon-gaps endpoint, where a curator decides. These assert the difference.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.core.database  # noqa: F401 — registers every mapper before select() runs
from src.learning.eval_gate import EvalGateError, GateResult, GroupDrop
from src.learning.learning_loop_worker import LearningLoopWorker
from src.models.lexicon_entry import LexiconEntry
from src.models.outbox import OutboxItem, OutboxKind


class FakeSession:
    """Records writes without needing a database."""

    def __init__(self):
        self.added: list = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    @property
    def lexicon_writes(self):
        return [o for o in self.added if isinstance(o, LexiconEntry)]

    @property
    def outbox_writes(self):
        return [o for o in self.added if isinstance(o, OutboxItem)]


def _report(recall):
    from src.learning.evaluator import EvalReport, Metrics

    report = EvalReport()
    tp = int(recall * 100)
    report.overall = Metrics(tp=tp, fn=100 - tp)
    return report


async def _one_decision(_stmt):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [SimpleNamespace(id=1)])
    )


@pytest.fixture
def worker():
    session = FakeSession()
    w = LearningLoopWorker(session)

    async def fake_generate(decision):
        return [
            LexiconEntry(
                term="مقترح",
                language="ar",
                category="slur",
                raw_target_group="yazidi",
                source="reviewer decision 1",
                enabled=True,
            )
        ], []

    w.generator = SimpleNamespace(generate_from_decision=fake_generate)
    session.execute = _one_decision
    return w, session


def _gate(result=None, error=None):
    async def run_evaluation(_changes):
        if error:
            raise error
        return result

    return SimpleNamespace(run_evaluation=run_evaluation)


async def test_a_passing_proposal_never_reaches_the_lexicon(worker):
    w, session = worker
    w.evaluator = _gate(GateResult(True, _report(0.71), _report(0.74)))

    await w.run_cycle()

    assert session.lexicon_writes == [], "a generated artifact reached the live rules"


async def test_a_passing_proposal_is_queued_for_the_curator(worker):
    w, session = worker
    w.evaluator = _gate(GateResult(True, _report(0.71), _report(0.74)))

    await w.run_cycle()

    queued = session.outbox_writes
    assert len(queued) == 1
    assert queued[0].kind == OutboxKind.LEXICON_GAP

    gap = queued[0].payload["gaps"][0]
    assert gap["suggested_term"] == "مقترح"
    assert gap["suggested_target_group"] == "yazidi"
    # The numbers are the part the agent is actually qualified to contribute.
    assert gap["gate_effect"] == {"recall_before": 0.71, "recall_after": 0.74}


async def test_a_failing_proposal_is_neither_applied_nor_queued(worker):
    w, session = worker
    w.evaluator = _gate(
        GateResult(False, _report(0.80), _report(0.60), [GroupDrop("yazidi", 0.9, 0.6)])
    )

    await w.run_cycle()

    assert session.added == []
    assert session.commits == 0


async def test_a_gate_that_cannot_judge_proposes_nothing(worker):
    """EvalGateError is a refusal to judge — never read it as approval."""
    w, session = worker
    w.evaluator = _gate(error=EvalGateError("gold set too small"))

    await w.run_cycle()

    assert session.added == []


async def test_a_gate_result_is_never_treated_as_a_bare_boolean(worker):
    """`if passed:` on a dataclass is always True — a failing gate would proceed."""
    w, session = worker
    failing = GateResult(False, _report(0.80), _report(0.10),
                         [GroupDrop("yazidi", 0.9, 0.1)])
    assert bool(failing) is True, "the object is truthy; .passed is what counts"

    w.evaluator = _gate(failing)
    await w.run_cycle()

    assert session.added == []

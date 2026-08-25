"""The learning loop must not write its own detection rules.

The lexicon is human-authored: curators fill the workbook, it imports to the
platform, the agent pulls it back. The worker used to session.add() and commit()
generated artifacts straight into the live rules, behind a gate that always
returned True — the agent editing the dictionary it is judged by (FR-LE-1).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import src.core.database  # noqa: F401 — registers every mapper before select() runs
from src.learning.eval_gate import EvalGateError, GateResult, GroupDrop
from src.learning.learning_loop_worker import LearningLoopWorker


class FakeSession:
    """Records writes without needing a database."""

    def __init__(self):
        self.added: list = []
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def execute(self, _stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


def _report(recall):
    from src.learning.evaluator import EvalReport, Metrics

    report = EvalReport()
    tp = int(recall * 100)
    report.overall = Metrics(tp=tp, fn=100 - tp)
    return report


@pytest.fixture
def worker():
    session = FakeSession()
    w = LearningLoopWorker(session)
    # One proposed term, so the cycle always reaches the gate.
    from src.models.lexicon_entry import LexiconEntry

    async def fake_generate(decision):
        return [LexiconEntry(term="مقترح", enabled=True)], []

    w.generator = SimpleNamespace(generate_from_decision=fake_generate)
    session.execute = _one_decision
    return w, session


async def _one_decision(_stmt):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [SimpleNamespace(id=1)])
    )


def _gate(result=None, error=None):
    async def run_evaluation(_changes):
        if error:
            raise error
        return result

    return SimpleNamespace(run_evaluation=run_evaluation)


async def test_a_passing_proposal_is_held_not_applied(worker):
    w, session = worker
    w.evaluator = _gate(GateResult(True, _report(0.80), _report(0.85)))

    await w.run_cycle()

    assert session.added == [], "a generated artifact reached the live rules"
    assert session.commits == 0


async def test_a_failing_proposal_is_not_applied(worker):
    w, session = worker
    w.evaluator = _gate(
        GateResult(False, _report(0.80), _report(0.60),
                   [GroupDrop("yazidi", 0.9, 0.6)])
    )

    await w.run_cycle()

    assert session.added == []
    assert session.commits == 0


async def test_a_gate_that_cannot_judge_does_not_apply(worker):
    """EvalGateError is a refusal to judge — never read it as approval."""
    w, session = worker
    w.evaluator = _gate(error=EvalGateError("gold set too small"))

    await w.run_cycle()

    assert session.added == []
    assert session.commits == 0


async def test_a_gate_result_is_never_treated_as_a_bare_boolean(worker):
    """`if passed:` on a dataclass is always True — a failing gate would apply."""
    w, session = worker
    failing = GateResult(False, _report(0.80), _report(0.10),
                         [GroupDrop("yazidi", 0.9, 0.1)])
    assert bool(failing) is True, "the object is truthy; .passed is what counts"

    w.evaluator = _gate(failing)
    await w.run_cycle()

    assert session.added == []

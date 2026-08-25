"""The eval gate.

It previously hardcoded baseline_recall=0.85 / proposed_recall=0.86 and never ran
the classifier, so it passed everything — in front of a path that wrote straight to
the live detection rules. These assert it can now fail, refuses to judge when it
cannot, and does not install what it is measuring.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from src.core.settings import get_settings
from src.learning.eval_gate import EvalGate, EvalGateError
from src.learning.evaluator import EvalReport, Metrics
from src.models.gold_eval_entry import GoldEvalEntry

PACK_DIR = Path(__file__).resolve().parents[3] / "packs" / "iraq-minorities"


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}")
    monkeypatch.setenv("GOLD_EVAL_MIN_SIZE", "10")
    monkeypatch.setenv("REGRESSION_MAX_DROP_PP", "2.0")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import get_session, init_db

    await init_db()
    async with get_session() as s:
        yield s

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


async def _fill_gold(session, count):
    for i in range(count):
        session.add(
            GoldEvalEntry(
                external_id=f"gold-{i}",
                text_content="نص للاختبار",
                parent_post_text="",
                target_group="yazidi",
                label="hate",
            )
        )
    await session.flush()


def _report(overall_recall, groups=None):
    report = EvalReport()
    # Metrics derives recall from tp/fn, so express the rate as counts.
    tp = int(round(overall_recall * 100))
    report.overall = Metrics(tp=tp, fn=100 - tp)
    for group, recall in (groups or {}).items():
        gtp = int(round(recall * 100))
        report.by_group[group] = Metrics(tp=gtp, fn=100 - gtp)
    return report


def _stub_eval(monkeypatch, reports):
    """Return baseline then proposed from a queue, so no LLM is called."""
    queue = list(reports)

    async def fake_run_eval(session, **kwargs):
        return queue.pop(0)

    monkeypatch.setattr("src.learning.eval_gate.run_eval", fake_run_eval)


def _proposal():
    from src.models.lexicon_entry import LexiconEntry

    return {"lexicon": [LexiconEntry(term="اختبار", enabled=True)], "tropes": []}


# ── Refusing to judge ────────────────────────────────────────────────────────


async def test_a_gold_set_below_the_minimum_is_not_a_pass(session):
    """The size check existed but its `return False` was commented out."""
    await _fill_gold(session, 3)

    with pytest.raises(EvalGateError, match="need 10"):
        await EvalGate(session).run_evaluation(_proposal())


async def test_an_empty_proposal_is_refused(session, monkeypatch):
    await _fill_gold(session, 12)
    _stub_eval(monkeypatch, [_report(0.80)])

    with pytest.raises(EvalGateError, match="nothing proposed"):
        await EvalGate(session).run_evaluation({"lexicon": [], "tropes": []})


async def test_a_broken_evaluation_is_refused_not_passed(session, monkeypatch):
    await _fill_gold(session, 12)
    broken = _report(0.0)
    broken.errors.append("classifier exploded")
    _stub_eval(monkeypatch, [broken])

    with pytest.raises(EvalGateError, match="baseline evaluation failed"):
        await EvalGate(session).run_evaluation(_proposal())


# ── Verdicts ─────────────────────────────────────────────────────────────────


async def test_an_improvement_passes(session, monkeypatch):
    await _fill_gold(session, 12)
    _stub_eval(monkeypatch, [
        _report(0.80, {"yazidi": 0.80}),
        _report(0.86, {"yazidi": 0.88}),
    ])

    result = await EvalGate(session).run_evaluation(_proposal())

    assert result.passed
    assert "+6.0pp" in result.summary


async def test_an_overall_recall_drop_fails(session, monkeypatch):
    await _fill_gold(session, 12)
    _stub_eval(monkeypatch, [_report(0.80), _report(0.70)])

    result = await EvalGate(session).run_evaluation(_proposal())

    assert not result.passed


async def test_a_single_group_regression_fails_even_when_overall_improves(
    session, monkeypatch
):
    """The aggregate hides exactly the failure this system exists to avoid."""
    await _fill_gold(session, 12)
    _stub_eval(monkeypatch, [
        _report(0.80, {"yazidi": 0.90, "christian-iraqi": 0.70}),
        _report(0.85, {"yazidi": 0.75, "christian-iraqi": 0.80}),
    ])

    result = await EvalGate(session).run_evaluation(_proposal())

    assert not result.passed
    assert [r.group for r in result.regressions] == ["yazidi"]
    assert "yazidi" in result.summary


async def test_a_drop_inside_the_threshold_passes(session, monkeypatch):
    await _fill_gold(session, 12)
    _stub_eval(monkeypatch, [
        _report(0.80, {"yazidi": 0.80}),
        _report(0.80, {"yazidi": 0.79}),  # 1pp, limit is 2pp
    ])

    assert (await EvalGate(session).run_evaluation(_proposal())).passed


async def test_a_group_with_no_positives_cannot_block(session, monkeypatch):
    """Otherwise nothing passes until the gold set covers all nine groups."""
    await _fill_gold(session, 12)
    baseline = _report(0.80, {"yazidi": 0.80})
    baseline.by_group["bahai"] = Metrics(tp=0, fn=0, fp=3)
    _stub_eval(monkeypatch, [baseline, _report(0.82, {"yazidi": 0.82})])

    assert (await EvalGate(session).run_evaluation(_proposal())).passed


# ── Measuring is not applying ────────────────────────────────────────────────


async def test_the_proposal_is_rolled_back_after_scoring(session, monkeypatch):
    """Without the savepoint, running the gate installs what it is judging."""
    from sqlalchemy import select

    from src.models.lexicon_entry import LexiconEntry

    await _fill_gold(session, 12)
    seen: dict[str, int] = {}

    async def fake_run_eval(s, **kwargs):
        rows = (await s.execute(select(LexiconEntry))).scalars().all()
        seen[f"pass{len(seen)}"] = len(rows)
        return _report(0.80 + 0.01 * len(seen))

    monkeypatch.setattr("src.learning.eval_gate.run_eval", fake_run_eval)

    await EvalGate(session).run_evaluation(_proposal())

    assert seen["pass0"] == 0, "baseline must not see the proposal"
    assert seen["pass1"] == 1, "proposed pass must see the proposal"

    remaining = (await session.execute(select(LexiconEntry))).scalars().all()
    assert remaining == [], "the proposal outlived the gate that was only measuring it"

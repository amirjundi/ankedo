"""Scoring and gating logic, independent of any model call."""
from __future__ import annotations

import src.core.database  # noqa: F401  — registers every mapper before instantiation
from src.learning.evaluator import EvalReport, Metrics, _score, cohens_kappa
from src.models.gold_eval_entry import GoldEvalEntry


def _entry(**kwargs) -> GoldEvalEntry:
    defaults = dict(
        text_content="t", parent_post_text="p", label="hate",
        target_group="yazidi", dialect="iraqi", annotators=[], hard_case=False,
    )
    return GoldEvalEntry(**{**defaults, "external_id": kwargs.pop("id", "x"), **kwargs})


def _result(verdict: str) -> dict:
    return {"verdict": verdict, "trace": {"specialist": {"rationale": "because"}}}


# ----------------------------------------------------------------- scoring


def test_correct_hate_is_a_true_positive():
    report = EvalReport()
    _score(report, _entry(label="hate"), _result("hate"))
    assert (report.overall.tp, report.overall.fp) == (1, 0)
    assert report.by_group["yazidi"].tp == 1


def test_flagging_benign_content_is_a_false_positive():
    report = EvalReport()
    _score(report, _entry(label="benign"), _result("hate"))
    assert report.overall.fp == 1
    assert report.failures[0]["kind"] == "fp"


def test_missing_hate_is_a_false_negative():
    report = EvalReport()
    _score(report, _entry(label="hate"), _result("benign"))
    assert report.overall.fn == 1


def test_ambiguous_gold_items_are_not_counted_as_errors():
    """FR-CL-11 routes genuine ambiguity to a human; scoring it wrong trains overconfidence."""
    report = EvalReport()
    _score(report, _entry(label="ambiguous"), _result("hate"))
    assert report.overall.abstained == 1
    assert report.overall.fp == 0
    assert report.failures == []


def test_model_abstention_is_not_an_error():
    report = EvalReport()
    _score(report, _entry(label="hate"), _result("ambiguous"))
    assert report.overall.abstained == 1
    assert report.overall.fn == 0


def test_hard_cases_and_counter_speech_are_tracked_separately():
    """Aggregate numbers can look fine while every hard case is wrong."""
    report = EvalReport()
    _score(report, _entry(label="benign", hard_case=True, category="counter_speech"),
           _result("hate"))
    assert report.hard_cases.fp == 1
    assert report.counter_speech.fp == 1


# ------------------------------------------------------------------ gating


def test_a_failing_group_fails_the_whole_gate():
    """NFR-SF-3: an average lets one community's failure hide behind another's success."""
    report = EvalReport()
    strong = Metrics(tp=50, fp=1, fn=1)
    weak = Metrics(tp=1, fp=9, fn=9)     # precision 0.10, recall 0.10
    report.overall = Metrics(tp=51, fp=10, fn=10)
    report.by_group["yazidi"] = strong
    report.by_group["shabak"] = weak

    assert report.overall.precision > 0.8
    assert report.meets(0.8, 0.8) is False, "a failing group must fail the gate"


def test_gate_passes_when_every_group_clears():
    report = EvalReport()
    report.overall = Metrics(tp=90, fp=5, fn=5)
    report.by_group["yazidi"] = Metrics(tp=45, fp=2, fn=2)
    report.by_group["shabak"] = Metrics(tp=45, fp=3, fn=3)
    assert report.meets(0.8, 0.8) is True


def test_groups_with_no_positives_do_not_break_the_gate():
    report = EvalReport()
    report.overall = Metrics(tp=10, fp=0, fn=0, tn=5)
    report.by_group["yazidi"] = Metrics(tp=10, fp=0, fn=0)
    report.by_group["bahai"] = Metrics(tn=5)  # benign-only slice
    assert report.meets(0.8, 0.8) is True


# ------------------------------------------------------------------- kappa


def test_kappa_needs_two_annotators():
    assert cohens_kappa([_entry(annotators=[{"id": "a", "label": "hate"}])]) == (None, 0)


def test_perfect_agreement():
    entries = [
        _entry(id=str(i), annotators=[{"id": "a", "label": lab}, {"id": "b", "label": lab}])
        for i, lab in enumerate(["hate", "benign", "hate", "benign"])
    ]
    kappa, n = cohens_kappa(entries)
    assert n == 4 and kappa == 1.0


def test_disagreement_lowers_kappa():
    entries = [
        _entry(id="1", annotators=[{"id": "a", "label": "hate"}, {"id": "b", "label": "benign"}]),
        _entry(id="2", annotators=[{"id": "a", "label": "benign"}, {"id": "b", "label": "hate"}]),
        _entry(id="3", annotators=[{"id": "a", "label": "hate"}, {"id": "b", "label": "hate"}]),
        _entry(id="4", annotators=[{"id": "a", "label": "benign"}, {"id": "b", "label": "benign"}]),
    ]
    kappa, _ = cohens_kappa(entries)
    assert kappa is not None and kappa < 0.6, "half-disagreement must land below the 0.6 floor"

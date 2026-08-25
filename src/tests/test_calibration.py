"""Confidence calibration.

The property that matters: calibration may change how confidences *spread*, never how
items *rank*. Ranking is what the eval measures, so a method that reorders items would
improve the calibration number while quietly degrading the classifier.
"""
from __future__ import annotations

import pytest

from src.learning.calibration import (
    apply_temperature,
    expected_calibration_error,
    fit_temperature,
)


def test_temperature_one_changes_nothing():
    for confidence in (0.1, 0.5, 0.9):
        assert apply_temperature(confidence, 1.0) == confidence


def test_higher_temperature_softens_overconfidence():
    assert apply_temperature(0.95, 2.0) < 0.95


def test_lower_temperature_sharpens():
    assert apply_temperature(0.7, 0.5) > 0.7


def test_ranking_is_preserved():
    """The invariant. Reordering would improve ECE while making the classifier worse."""
    scores = [0.1, 0.3, 0.5, 0.7, 0.9]
    for temperature in (0.5, 1.5, 3.0):
        scaled = [apply_temperature(s, temperature) for s in scores]
        assert scaled == sorted(scaled)


def test_a_half_confidence_is_a_fixed_point():
    """0.5 is the point of no information; scaling must not push it either way."""
    for temperature in (0.5, 2.0, 5.0):
        assert apply_temperature(0.5, temperature) == pytest.approx(0.5, abs=1e-9)


def test_extremes_stay_in_range():
    for temperature in (0.1, 10.0):
        assert 0.0 < apply_temperature(0.999, temperature) < 1.0
        assert 0.0 < apply_temperature(0.001, temperature) < 1.0


# ------------------------------------------------------------------ fitting


def test_overconfident_model_gets_a_temperature_above_one():
    """Claims 0.95, right 60% of the time — the standard LLM failure."""
    pairs = [(0.95, i < 6) for i in range(10)] * 5
    assert fit_temperature(pairs) > 1.0


def test_well_calibrated_model_needs_little_correction():
    pairs = [(0.9, i < 9) for i in range(10)] * 5
    assert fit_temperature(pairs) == pytest.approx(1.0, abs=0.6)


def test_too_little_data_is_a_no_op():
    """Fitting on three items would produce a confident, meaningless number."""
    assert fit_temperature([(0.9, True), (0.8, False), (0.7, True)]) == 1.0


# --------------------------------------------------------------------- ECE


def test_perfect_calibration_scores_zero():
    pairs = [(1.0, True)] * 10 + [(0.0, False)] * 10
    error, _ = expected_calibration_error(pairs)
    assert error == pytest.approx(0.0, abs=1e-9)


def test_overconfidence_is_measured():
    """Claims 0.9, right half the time — the gap should read around 0.4."""
    pairs = [(0.9, i % 2 == 0) for i in range(20)]
    error, _ = expected_calibration_error(pairs)
    assert error == pytest.approx(0.4, abs=0.05)


def test_fitting_reduces_the_error_it_measures():
    pairs = [(0.95, i < 6) for i in range(10)] * 5
    before, _ = expected_calibration_error(pairs)
    temperature = fit_temperature(pairs)
    after, _ = expected_calibration_error(
        [(apply_temperature(c, temperature), ok) for c, ok in pairs]
    )
    assert after < before


def test_bins_report_claimed_against_actual():
    """The number that answers 'when it says 0.9, how often is it right?'"""
    pairs = [(0.9, i % 2 == 0) for i in range(20)]
    _, bins = expected_calibration_error(pairs)
    bucket = next(b for b in bins if b["count"])
    assert bucket["claimed"] == pytest.approx(0.9)
    assert bucket["actual"] == pytest.approx(0.5)

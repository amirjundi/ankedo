"""Merging image and text verdicts.

The case that drives this: a meme whose caption is innocuous and whose picture carries
the attack. Captions are frequently chosen that way, so a text-only pipeline clears
exactly the posts that matter most.
"""
from __future__ import annotations

import pytest

from src.classifiers.media_analyzer import merge_with_text


def _text(verdict="benign", confidence=0.9, severity=0):
    return {
        "verdict": verdict,
        "hate_speech_flag": verdict == "hate",
        "classification_score": confidence if verdict == "hate" else 0.0,
        "confidence": confidence,
        "severity": severity,
        "category": "none",
        "committee_disagreement": False,
        "trace": {},
    }


def _media(verdict="hate", confidence=0.88, severity=4):
    return {
        "verdict": verdict,
        "confidence": confidence,
        "category": "dehumanization",
        "severity": severity,
        "visible_text": "",
        "imagery_description": "community symbol beside vermin imagery",
        "rationale": "dehumanizing juxtaposition",
    }


def test_hateful_image_overrides_an_innocuous_caption():
    """The whole reason multimodal exists."""
    merged = merge_with_text(_text("benign"), _media("hate"))

    assert merged["verdict"] == "hate"
    assert merged["hate_speech_flag"] is True
    assert merged["decided_by"] == "media"


def test_benign_image_does_not_soften_hateful_text():
    """A pleasant picture must not launder a hateful caption."""
    merged = merge_with_text(_text("hate", 0.95, severity=4), _media("benign", 0.9, severity=0))

    assert merged["verdict"] == "hate"
    assert merged["decided_by"] == "text"


def test_ambiguous_image_lifts_a_benign_text_verdict():
    merged = merge_with_text(_text("benign"), _media("ambiguous", 0.5, severity=1))
    assert merged["verdict"] == "ambiguous"


def test_severity_takes_the_higher_of_the_two():
    merged = merge_with_text(_text("benign", severity=1), _media("hate", severity=4))
    assert merged["severity"] == 4


def test_media_analysis_is_kept_in_the_result():
    """A reviewer needs the description, not just a changed verdict."""
    merged = merge_with_text(_text(), _media())
    assert merged["media_analysis"]["imagery_description"]


def test_no_media_leaves_the_text_verdict_untouched():
    text = _text("hate", 0.8)
    assert merge_with_text(text, None) == text


def test_failed_media_analysis_does_not_alter_the_verdict():
    """Losing the image is recoverable; treating failure as 'nothing found' is not."""
    text = _text("benign")
    merged = merge_with_text(text, {"analysis_failed": True, "error": "quota"})
    assert merged == text


def test_both_hateful_keeps_the_text_path():
    merged = merge_with_text(_text("hate", 0.9, severity=3), _media("hate", 0.7, severity=2))
    assert merged["verdict"] == "hate"
    assert merged["decided_by"] == "text"


@pytest.mark.parametrize(
    "text_verdict,media_verdict,expected",
    [
        ("benign", "benign", "benign"),
        ("benign", "ambiguous", "ambiguous"),
        ("benign", "hate", "hate"),
        ("ambiguous", "benign", "ambiguous"),
        ("ambiguous", "hate", "hate"),
        ("hate", "benign", "hate"),
        ("hate", "ambiguous", "hate"),
    ],
)
def test_the_more_severe_verdict_always_wins(text_verdict, media_verdict, expected):
    merged = merge_with_text(_text(text_verdict), _media(media_verdict))
    assert merged["verdict"] == expected

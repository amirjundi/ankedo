"""Verdict and scan-log payload construction.

Tested because these are the shapes the platform stores and reports on, and several
fields fail silently rather than loudly when they are wrong — a null severity column
or an inverted density figure looks like data, not like a bug.
"""
from __future__ import annotations

from src.classifiers.context_bundle import ContextBundle
from src.core.collection_runner import CollectionStats
from src.ettok.submit import build_scan_log, build_verdict, severity_label

PIOUS = "اعوذ بالله من الشيطان الرجيم"


def _bundle():
    return ContextBundle(
        comment_text=PIOUS,
        parent_post_text="مراسم دينية إيزيدية في معبد لالش",
        target_groups=["yazidi"],
        dialect="iraqi",
        platform="facebook",
    )


def _result(**overrides):
    base = {
        "verdict": "hate",
        "confidence": 0.873,
        "severity": 4,
        "category": "dehumanization",
        "relies_on_context": True,
        "committee_disagreement": False,
        "trace": {
            "lexicon_hits": [{"term": "x", "pack_version": "2.1"}],
            "tropes_fired": [
                {
                    "trope_id": "yazidi-devil-worship",
                    "surface_form": PIOUS,
                    "reason": "activation satisfied: content concerns yazidi",
                    "pack_version": "1.4",
                }
            ],
            "specialist": {
                "model": "gemini-3.6-flash",
                "prompt_version": "specialist-v1",
                "rationale": "invokes the devil-worship libel",
            },
            "critic": {"model": "gemini-3.5-flash-lite", "concern": None},
        },
    }
    return {**base, **overrides}


# --------------------------------------------------------------- severity


def test_severity_is_sent_as_the_string_the_dashboard_filters_on():
    """An int lands as "4", fails the choice constraint, and nulls the column."""
    item = build_verdict(bundle=_bundle(), result=_result(), url="https://x/1")
    assert item["severity"] in ("low", "medium", "high")
    assert item["severity_score"] == 4, "the 1-10 scale travels alongside, not instead"


def test_every_severity_score_maps_to_a_valid_choice():
    assert {severity_label(s) for s in range(5)} <= {"low", "medium", "high"}


def test_missing_severity_does_not_produce_an_invalid_choice():
    item = build_verdict(bundle=_bundle(), result=_result(severity=None), url="https://x/1")
    assert item["severity"] in ("low", "medium", "high")


# ------------------------------------------------------------ safety flags


def test_committee_disagreement_is_carried():
    """The signal telling a reviewer they are the tiebreaker."""
    item = build_verdict(
        bundle=_bundle(), result=_result(committee_disagreement=True), url="https://x/1"
    )
    assert item["committee_disagreement"] is True


def test_ambiguous_verdicts_travel_as_ambiguous():
    """Never coerced to hate or benign — FR-CL-11 routes these to a human."""
    item = build_verdict(bundle=_bundle(), result=_result(verdict="ambiguous"), url="https://x/1")
    assert item["verdict"] == "ambiguous"


def test_versions_are_present_for_reproducibility():
    """FR-CL-14: without these a verdict cannot be defended months later."""
    versions = build_verdict(bundle=_bundle(), result=_result(), url="https://x/1")["versions"]
    assert versions["specialist_model"] == "gemini-3.6-flash"
    assert versions["prompt_version"] == "specialist-v1"
    assert versions["lexicon_version"] == "2.1"
    assert versions["trope_version"] == "1.4"


# ---------------------------------------------------------------- context


def test_content_type_is_always_sent():
    """It defaults to 'post' upstream, and most items are comments."""
    assert build_verdict(bundle=_bundle(), result=_result(), url="https://x/1")["content_type"] == "comment"
    assert (
        build_verdict(bundle=_bundle(), result=_result(), url="https://x/1", is_comment=False)[
            "content_type"
        ]
        == "post"
    )


def test_parent_post_and_groups_travel_with_the_verdict():
    item = build_verdict(bundle=_bundle(), result=_result(), url="https://x/1")
    assert item["parent_post_text"], "the verdict is meaningless without what it replied to"
    assert item["target_groups"] == ["yazidi"]


def test_fired_tropes_carry_their_activation_reason():
    fired = build_verdict(bundle=_bundle(), result=_result(), url="https://x/1")["fired_tropes"]
    assert fired[0]["trope"] == "yazidi-devil-worship"
    assert "yazidi" in fired[0]["activation"]


def test_why_flagged_prefers_the_trope_over_the_model():
    item = build_verdict(bundle=_bundle(), result=_result(), url="https://x/1")
    assert "yazidi-devil-worship" in item["why_flagged"]


def test_image_decided_verdicts_say_so_first():
    """The case most likely to be dismissed: the caption reads as innocuous.

    A reviewer seeing only "model verdict hate at 0.88" alongside harmless text has
    every reason to call it a false positive.
    """
    result = _result(verdict="hate", decided_by="media")
    result["media_analysis"] = {"imagery_description": "community symbol beside vermin imagery"}
    result["trace"]["tropes_fired"] = []
    result["trace"]["lexicon_hits"] = []

    item = build_verdict(bundle=_bundle(), result=result, url="https://x/1")

    assert "IMAGE" in item["why_flagged"]
    assert "vermin" in item["why_flagged"]
    assert item["decided_by"] == "media"


def test_disagreement_is_explained_when_nothing_else_fired():
    result = _result(committee_disagreement=True)
    result["trace"]["tropes_fired"] = []
    result["trace"]["lexicon_hits"] = []

    item = build_verdict(bundle=_bundle(), result=result, url="https://x/1")
    assert "disagreed" in item["why_flagged"]


def test_why_flagged_falls_back_to_the_model_verdict():
    result = _result()
    result["trace"]["tropes_fired"] = []
    result["trace"]["lexicon_hits"] = []
    item = build_verdict(bundle=_bundle(), result=result, url="https://x/1")
    assert "model verdict" in item["why_flagged"]


# --------------------------------------------------------------- scan log


def test_scan_log_carries_the_denominator():
    """Without comments_scanned, hate density is uncomputable on the platform side."""
    stats = CollectionStats(posts_scanned=300, comments_scanned=8420, accounts_attempted=12)
    log = build_scan_log(stats, duration_seconds=610, platforms=["facebook"])

    assert log["comments_scanned"] == 8420
    assert log["posts_scanned"] == 300


def test_density_from_the_scan_log_is_not_inverted():
    """The failure this field prevents: 4 flagged of 8,420 is 0.05%, not 100%."""
    stats = CollectionStats(posts_scanned=300, comments_scanned=8420)
    log = build_scan_log(stats, duration_seconds=610, platforms=["facebook"])
    log["items_flagged"] = 4

    density = log["items_flagged"] / log["comments_scanned"]
    assert density < 0.001


def test_coverage_reports_what_was_missed():
    stats = CollectionStats(accounts_attempted=12, accounts_blocked=1, accounts_captcha=1)
    coverage = build_scan_log(stats, duration_seconds=1, platforms=["facebook"])["coverage"]

    assert coverage["accounts_attempted"] == 12
    assert coverage["accounts_blocked"] == 1
    assert coverage["accounts_captcha"] == 1


def test_per_platform_breakdown_is_included():
    stats = CollectionStats()
    stats.bump("facebook", "comments_scanned", 40)
    stats.bump("instagram", "comments_scanned", 12)
    log = build_scan_log(stats, duration_seconds=1, platforms=["facebook", "instagram"])

    assert log["per_platform"]["facebook"]["comments_scanned"] == 40
    assert log["per_platform"]["instagram"]["comments_scanned"] == 12


def test_errors_are_reported_not_swallowed():
    stats = CollectionStats(errors=["instagram: no healthy worker account"])
    log = build_scan_log(stats, duration_seconds=1, platforms=["instagram"])
    assert log["errors"] == ["instagram: no healthy worker account"]

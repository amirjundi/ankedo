"""Build and send submissions to the platform.

Two payloads, both shaped by decisions recorded in specs/005:

**Verdicts** (§7). The agent decides; Ettok displays, reports, and holds the reviewer
queue. FR-RV-5 is untouched — nothing reaches a platform report without a human moving
it through review. Two fields do safety work rather than carrying data:
`committee_disagreement` and an `ambiguous` verdict both mean *the agent deliberately
declined to resolve this*, and a reviewer needs to see that.

**Scan logs** (§1). The counts a monitoring programme is judged on. `comments_scanned`
is the denominator and it exists only here — the platform never learns what was looked
at and cleared, only what was flagged. Without it, hate density is uncomputable and the
numbers invert: a run flagging 4 items out of 8,420 comments reads as 100% rather than
0.05%.

Compatibility shims for the current Ettok schema, agreed with that side:
* `severity` goes as the string the dashboard already filters on, with the numeric
  score alongside
* `content_type` is always sent, because it defaults to "post" and most items are
  comments
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import structlog

from src.classifiers.context_bundle import ContextBundle
from src.core.collection_runner import CollectionStats
from src.ettok.client import EttokClient

log = structlog.get_logger()

# The dashboard, MonthlyReport.by_severity and the review queue all key on these.
_SEVERITY_BANDS = {0: "low", 1: "low", 2: "medium", 3: "high", 4: "high"}


def severity_label(score: int | None) -> str:
    return _SEVERITY_BANDS.get(score or 0, "medium")


def build_verdict(
    *,
    bundle: ContextBundle,
    result: dict,
    url: str,
    comment_url: str | None = None,
    author_name: str | None = None,
    author_id: str | None = None,
    posted_at: str | None = None,
    collected_at: str | None = None,
    screenshots: list[dict] | None = None,
    is_comment: bool = True,
) -> dict:
    """Assemble one §7 verdict item."""
    trace = result.get("trace") or {}
    specialist = trace.get("specialist") or {}
    critic = trace.get("critic") or {}

    return {
        "platform": bundle.platform,
        "url": url,
        "comment_url": comment_url,
        # Defaults to "post" upstream; most of what we collect is comments.
        "content_type": "comment" if is_comment else "post",
        "text": bundle.comment_text,
        "author_name": author_name or "",
        "author_id": author_id or "",
        "posted_at": posted_at,
        "collected_at": collected_at,
        # Structured context — the platform should not have to re-derive what the
        # comment was replying to in order to understand the verdict.
        "parent_post_text": bundle.parent_post_text,
        "target_groups": bundle.target_groups,
        "thread_context": bundle.thread_context,
        "dialect": bundle.dialect,
        "matched_terms": [hit.get("term") for hit in trace.get("lexicon_hits") or []],
        "fired_tropes": [
            {
                "trope": trope.get("trope_id"),
                "surface_form": trope.get("surface_form"),
                "activation": trope.get("reason"),
            }
            for trope in trace.get("tropes_fired") or []
        ],
        "verdict": result["verdict"],
        "category": result.get("category"),
        "severity": severity_label(result.get("severity")),
        "severity_score": result.get("severity"),
        "confidence": round(float(result.get("confidence") or 0.0), 3),
        # A calibrated 0.9 means "right about 90% of the time"; a raw 0.9 means "the
        # model felt strongly", and LLMs are systematically overconfident. Both would
        # render as the same number in a review queue, so the scale travels with it —
        # a reviewer deciding whether to report a person needs to know which claim
        # they are reading.
        "confidence_scale": _confidence_scale(trace),
        "confidence_raw": _raw_confidence(trace),
        "relies_on_context": result.get("relies_on_context", False),
        # Safety-critical: the agent chose not to resolve this. A reviewer seeing an
        # ordinary-looking borderline item would lose that.
        "committee_disagreement": result.get("committee_disagreement", False),
        "rationale": specialist.get("rationale") or "",
        "critic_concern": critic.get("concern"),
        # FR-CL-14 — without these a verdict cannot be reproduced or defended later.
        "versions": {
            "specialist_model": specialist.get("model"),
            "prompt_version": specialist.get("prompt_version"),
            "critic_model": critic.get("model"),
            "lexicon_version": _version(trace, "lexicon_hits"),
            "trope_version": _version(trace, "tropes_fired"),
        },
        # Which signal actually decided it — "text" or "media".
        "decided_by": result.get("decided_by", "text"),
        "media_analysis": result.get("media_analysis"),
        "screenshots": screenshots or [],
        "why_flagged": _why(trace, result),
    }


def build_scan_log(stats: CollectionStats, *, duration_seconds: int, platforms: list[str]) -> dict:
    """Assemble the §1 scan log, denominators included."""
    return {
        "platforms_browsed": platforms,
        "posts_scanned": stats.posts_scanned,
        "comments_scanned": stats.comments_scanned,
        "posts_with_flagged_comments": stats.per_platform.get("_flagged_posts", 0),
        "items_flagged": 0,  # filled by the caller once verdicts are known
        "per_platform": {k: v for k, v in stats.per_platform.items() if not k.startswith("_")},
        # What was NOT looked at. A report that cannot state its own gaps invites the
        # objection that absence of evidence was treated as evidence of absence.
        "coverage": {
            "accounts_attempted": stats.accounts_attempted,
            "accounts_blocked": stats.accounts_blocked,
            "accounts_captcha": stats.accounts_captcha,
            "vision_fallbacks": stats.used_vision,
        },
        "duration_seconds": duration_seconds,
        "errors": stats.errors,
    }


def hash_file(path: str | Path) -> str:
    """SHA-256 so the platform can verify an image was not altered in transit.

    Also the first step toward sealed evidence: a hash computed at capture time is
    what later lets someone prove the screenshot is the one that was taken.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def submit_verdicts(client: EttokClient, items: list[dict]) -> dict:
    """Send verdicts. Returns the platform's acknowledgement."""
    if not items:
        return {"accepted": 0}
    response = await client.post_flagged_items(items)
    log.info("Verdicts submitted", count=len(items), accepted=response.get("accepted"))
    return response


def _confidence_scale(trace: dict) -> str:
    """"calibrated" once a temperature has been fitted, else "raw"."""
    calibration = trace.get("calibration") or {}
    temperature = calibration.get("temperature")
    return "calibrated" if temperature and temperature != 1.0 else "raw"


def _raw_confidence(trace: dict) -> float | None:
    """The pre-calibration score, so a past verdict stays reconstructable."""
    calibration = trace.get("calibration") or {}
    raw = calibration.get("raw")
    return round(float(raw), 3) if raw is not None else None


def _version(trace: dict, key: str) -> str | None:
    for entry in trace.get(key) or []:
        if entry.get("pack_version"):
            return entry["pack_version"]
    return None


def _why(trace: dict, result: dict) -> str:
    """One line a reviewer can act on.

    The image-decided case leads, because it is the one most likely to be dismissed
    as a false positive: the reviewer reads an innocuous caption first and sees
    nothing wrong. If the explanation does not say the picture drove the verdict,
    the caption is all they have to go on.
    """
    if result.get("decided_by") == "media":
        media = result.get("media_analysis") or {}
        return (
            "the IMAGE drove this verdict, not the text — "
            f"{media.get('imagery_description') or media.get('rationale') or 'see media analysis'}"
        )

    fired = trace.get("tropes_fired") or []
    if fired:
        return f"trope {fired[0].get('trope_id')} activated: {fired[0].get('reason')}"
    hits = trace.get("lexicon_hits") or []
    if hits:
        return f"lexicon term {hits[0].get('term')!r} matched"
    if result.get("committee_disagreement"):
        return "two independent passes disagreed — this needs a human decision"
    return f"model verdict {result['verdict']} at {result.get('confidence')} confidence"

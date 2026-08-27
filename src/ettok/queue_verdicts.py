"""Put finished verdicts into the outbox.

This is the join that was missing. `build_verdict` assembled a §7 item and
`submit_verdicts` posted one, the outbox stored and retried one, `drain` sent one —
and none of them had a caller. The agent classified content and the result stopped at
the local database. Every part of the road existed except the junction.

**Why the outbox rather than posting here.** Classification holds an open transaction
over the post and its comments. Posting inside it would either hold that transaction
open across a network call on a connection that drops, or commit and then discover the
send failed with nothing recording that it must be retried. Writing a row commits with
the verdicts, atomically, and the sender picks it up afterwards.

**What gets sent.** Flagged items, and items the agent declined to resolve. A cleared
comment is not submitted — the platform never receives what was looked at and found
benign, only the count of it, which travels in the scan log as the denominator. That
asymmetry is deliberate: a monitoring programme that uploaded every comment it read
would be building a surveillance archive of ordinary people, which is not what this is
for.
"""
from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.classifiers.context_bundle import ContextBundle
from src.ettok.outbox import enqueue
from src.ettok.submit import build_verdict
from src.models.comment import Comment
from src.models.outbox import OutboxKind
from src.models.post import Post

log = structlog.get_logger()


def is_submittable(result: dict) -> bool:
    """Flagged, or deliberately unresolved.

    `committee_disagreement` and an `ambiguous` verdict both mean the agent chose not
    to decide. Those must reach a human, and dropping them because the flag came back
    false would silently discard exactly the cases that most need looking at.
    """
    return bool(
        result.get("hate_speech_flag")
        or result.get("verdict") == "ambiguous"
        or result.get("committee_disagreement")
    )


def verdict_for_comment(
    *, post: Post, comment: Comment, bundle: ContextBundle, result: dict
) -> dict:
    return build_verdict(
        bundle=bundle,
        result=result,
        url=post.url,
        # No per-comment permalink is captured, so the post URL locates it. Sending
        # the post URL in `url` and leaving `comment_url` unset is honest about that;
        # inventing a fragment would give the platform a link that 404s.
        author_name=comment.author_name,
        posted_at=_iso(getattr(comment, "posted_at", None)),
        collected_at=_iso(getattr(comment, "collected_at", None)),
        is_comment=True,
    )


def verdict_for_post(*, post: Post, bundle: ContextBundle, result: dict) -> dict:
    return build_verdict(
        bundle=bundle,
        result=result,
        url=post.url,
        author_name=post.author_name,
        posted_at=_iso(getattr(post, "posted_at", None)),
        collected_at=_iso(getattr(post, "collected_at", None)),
        is_comment=False,
    )


async def queue_verdicts(session: AsyncSession, items: list[dict]) -> None:
    """One outbox row per post, carrying every submittable verdict from it.

    Batched rather than one row per comment: the platform accepts a list, and a post
    with forty flagged comments would otherwise become forty HTTP requests over a
    connection that is the reason this queue exists.
    """
    if not items:
        return
    await enqueue(session, OutboxKind.VERDICT, {"items": items})
    log.info("Verdicts queued for the platform", count=len(items))


def _iso(value) -> str | None:
    """Datetimes go over the wire as ISO 8601; anything else passes through."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)

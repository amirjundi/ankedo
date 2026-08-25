"""Capture endpoint for the optional Chrome extension.

The extension reads a post the operator is already looking at, in their own logged-in
browser, and posts it here. That solves two problems at once: it works on a machine
where Playwright has no browser to install, and it needs no stored platform password,
no worker-account warm-up, and no anti-detect launcher — it is a person browsing.

**Optional by construction.** The router is only mounted when `EXTENSION_ENABLED` is
true. An installation that does not use the extension does not merely leave these
endpoints unused; it does not have them. They accept content into the classification
pipeline, and an endpoint nobody knows is there is the one nobody notices being called.

**A new producer, not a new pipeline.** Captured content becomes a `Post` in
`DISCOVERY` with its `Comment` rows, enqueued exactly as `CollectionRunner` does, so
the existing queue, classifier, committee and evidence path handle it unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import session_scope
from src.core.queue_manager import QueueManager
from src.core.settings import get_settings
from src.models.comment import Comment
from src.models.post import Post
from src.models.tracked_account import AccountSource, AccountStatus, TrackedAccount

log = structlog.get_logger()
router = APIRouter(prefix="/api/extension", tags=["extension"])

# Only platforms with an adapter, so a capture cannot create rows the rest of the
# system has no idea how to handle.
SUPPORTED_PLATFORMS = {"facebook", "instagram", "tiktok"}

# A capture is one screenful of a thread, not a crawl. The cap is here so a malformed
# or hostile payload cannot enqueue ten thousand classification calls.
MAX_COMMENTS = 500


class CapturedComment(BaseModel):
    platform_comment_id: str = Field(max_length=255)
    text: str | None = Field(default=None, max_length=20000)
    author_name: str | None = Field(default=None, max_length=255)


class CaptureRequest(BaseModel):
    platform: str = Field(max_length=50)
    url: str = Field(max_length=1024)
    platform_post_id: str = Field(max_length=255)
    content_text: str | None = Field(default=None, max_length=50000)
    author_name: str | None = Field(default=None, max_length=255)
    author_handle: str | None = Field(default=None, max_length=255)
    media_urls: list[str] = Field(default_factory=list)
    comments: list[CapturedComment] = Field(default_factory=list)


class CaptureResponse(BaseModel):
    post_id: str
    comments_added: int
    duplicate: bool
    queued: bool


@router.get("/status")
async def status_endpoint():
    """So the extension can tell it is talking to a live agent that wants captures."""
    settings = get_settings()
    return {
        "enabled": True,
        "agent_id": settings.ettok_agent_id,
        "platforms": sorted(SUPPORTED_PLATFORMS),
        "max_comments": MAX_COMMENTS,
    }


async def _account_for(session: AsyncSession, platform: str, handle: str, name: str | None):
    """Find or create the tracked account a captured post hangs off.

    Post.tracked_account_id is not nullable, and a captured post is usually from a page
    nobody added to the watch list yet. Creating it as Manual/Warmup records where the
    content came from without silently promoting a page into the crawl rotation — an
    operator reading one post should not start an unattended crawl of its author.
    """
    existing = (
        await session.execute(
            select(TrackedAccount).where(
                TrackedAccount.platform == platform,
                TrackedAccount.handle == handle,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    account = TrackedAccount(
        platform=platform,
        handle=handle,
        display_name=name,
        status=AccountStatus.WARMUP,
        source=AccountSource.MANUAL,
        first_seen_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(account)
    await session.flush()
    log.info("Tracked account created from a capture", platform=platform, handle=handle)
    return account


@router.post("/capture", response_model=CaptureResponse)
async def capture(request: CaptureRequest, session: AsyncSession = Depends(session_scope)):
    platform = request.platform.strip().lower()
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported platform {platform!r}. Known: {', '.join(sorted(SUPPORTED_PLATFORMS))}",
        )

    handle = (request.author_handle or request.author_name or "unknown").strip()[:255]
    account = await _account_for(session, platform, handle, request.author_name)

    existing = (
        await session.execute(
            select(Post).where(
                Post.platform == platform,
                Post.platform_post_id == request.platform_post_id,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        # Re-capturing a thread is how an operator adds comments that loaded after
        # the first pass, so new comments are merged rather than the post refused.
        added = await _add_comments(session, existing, request.comments)
        await session.commit()
        log.info("Capture merged into an existing post", post_id=existing.id, added=added)
        return CaptureResponse(
            post_id=existing.id, comments_added=added, duplicate=True, queued=False
        )

    post = Post(
        tracked_account_id=account.id,
        case_id=account.linked_case_id,
        platform=platform,
        platform_post_id=request.platform_post_id,
        url=request.url,
        content_text=request.content_text,
        content_media_urls=request.media_urls[:50],
        author_name=request.author_name,
        collected_at=datetime.now(timezone.utc).isoformat(),
    )
    session.add(post)
    await session.flush()

    added = await _add_comments(session, post, request.comments)
    await session.commit()

    # Straight onto the existing queue — the classifier does not care that a person
    # rather than a crawler put it there.
    await QueueManager(session).enqueue_discovery(
        tracked_account_id=account.id, post_id=post.id, case_id=account.linked_case_id
    )

    log.info("Captured a post from the extension", post_id=post.id, comments=added)
    return CaptureResponse(post_id=post.id, comments_added=added, duplicate=False, queued=True)


async def _add_comments(session: AsyncSession, post: Post, comments: list[CapturedComment]) -> int:
    """Insert the comments not already recorded against this post."""
    if not comments:
        return 0

    seen = {
        row
        for row in (
            await session.execute(
                select(Comment.platform_comment_id).where(Comment.post_id == post.id)
            )
        ).scalars()
    }

    added = 0
    for comment in comments[:MAX_COMMENTS]:
        if comment.platform_comment_id in seen:
            continue
        seen.add(comment.platform_comment_id)
        session.add(
            Comment(
                post_id=post.id,
                platform_comment_id=comment.platform_comment_id,
                text=comment.text,
                author_name=comment.author_name,
            )
        )
        added += 1

    post.comments_total = (post.comments_total or 0) + added
    return added

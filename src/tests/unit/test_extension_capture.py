"""The optional extension capture endpoint.

Two things must hold: it is genuinely absent when disabled, and a capture lands in the
existing pipeline rather than a parallel one.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.core.settings import get_settings
from src.models.comment import Comment
from src.models.post import Post, QueueState
from src.models.queue_item import QueueItem
from src.models.tracked_account import AccountSource, TrackedAccount


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'ext.db'}")
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


def _payload(**over):
    body = {
        "platform": "facebook",
        "url": "https://facebook.com/somepage/posts/123",
        "platform_post_id": "123",
        "content_text": "منشور عن مجتمع الإيزيديين",
        "author_name": "Some Page",
        "author_handle": "somepage",
        "media_urls": [],
        "comments": [
            {"platform_comment_id": "c1", "text": "تعليق أول", "author_name": "A"},
            {"platform_comment_id": "c2", "text": "تعليق ثانٍ", "author_name": "B"},
        ],
    }
    body.update(over)
    return body


async def _capture(session, **over):
    from src.api.extension_router import CaptureRequest, capture

    return await capture(CaptureRequest(**_payload(**over)), session)


# ── Optional means absent ────────────────────────────────────────────────────


def test_the_router_is_not_mounted_when_disabled(monkeypatch):
    monkeypatch.setenv("EXTENSION_ENABLED", "false")
    get_settings.cache_clear()

    import importlib

    import src.api.main as main

    importlib.reload(main)
    # Included routers are not flattened into app.routes in this FastAPI version;
    # the OpenAPI schema is what actually reflects the mounted endpoints.
    paths = set(main.app.openapi()["paths"])

    assert not any(p.startswith("/api/extension") for p in paths)
    get_settings.cache_clear()


def test_the_router_is_mounted_when_enabled(monkeypatch):
    monkeypatch.setenv("EXTENSION_ENABLED", "true")
    get_settings.cache_clear()

    import importlib

    import src.api.main as main

    importlib.reload(main)
    # Included routers are not flattened into app.routes in this FastAPI version;
    # the OpenAPI schema is what actually reflects the mounted endpoints.
    paths = set(main.app.openapi()["paths"])

    assert "/api/extension/capture" in paths
    get_settings.cache_clear()
    importlib.reload(main)


def test_a_blank_extension_origin_does_not_widen_cors(monkeypatch):
    """An empty setting must not mean 'trust every installed extension'."""
    monkeypatch.setenv("EXTENSION_ENABLED", "true")
    monkeypatch.delenv("EXTENSION_ORIGIN", raising=False)
    get_settings.cache_clear()

    import importlib

    import src.api.main as main

    importlib.reload(main)

    origins = [o for m in main.app.user_middleware
               for o in getattr(m.kwargs.get("allow_origins", []), "__iter__", list)()]
    assert not any(str(o).startswith("chrome-extension") for o in origins)
    assert "*" not in origins
    get_settings.cache_clear()
    importlib.reload(main)


# ── A capture joins the existing pipeline ────────────────────────────────────


async def test_a_capture_creates_a_post_with_its_comments(session):
    result = await _capture(session)

    assert result.duplicate is False
    assert result.comments_added == 2

    post = (await session.execute(select(Post))).scalar_one()
    assert post.platform_post_id == "123"
    assert post.content_text.startswith("منشور")
    assert post.collected_at

    comments = (await session.execute(select(Comment))).scalars().all()
    assert {c.text for c in comments} == {"تعليق أول", "تعليق ثانٍ"}


async def test_a_capture_is_queued_for_classification(session):
    """The point of the feature: captured content is classified like crawled content."""
    result = await _capture(session)

    item = (await session.execute(select(QueueItem))).scalar_one()
    assert item.post_id == result.post_id
    assert item.is_inflight is False


async def test_the_post_starts_at_classification(session):
    """Not Discovery: that stage waits for a browser to fetch comments a capture
    already brought with it, so an item parked there never advances."""
    await _capture(session)

    post = (await session.execute(select(Post))).scalar_one()
    assert post.queue_state == QueueState.CLASSIFICATION


async def test_an_unknown_author_becomes_a_manual_account_not_a_crawl_target(session):
    """Reading one post must not start an unattended crawl of its author."""
    await _capture(session)

    account = (await session.execute(select(TrackedAccount))).scalar_one()
    assert account.handle == "somepage"
    assert account.source == AccountSource.MANUAL
    assert account.status != "Active"


async def test_recapturing_merges_new_comments_instead_of_duplicating(session):
    """Comments load lazily; a second capture after expanding them must not duplicate."""
    await _capture(session)

    again = await _capture(
        comments=[
            {"platform_comment_id": "c1", "text": "تعليق أول", "author_name": "A"},
            {"platform_comment_id": "c3", "text": "تعليق ثالث", "author_name": "C"},
        ],
        session=session,
    )

    assert again.duplicate is True
    assert again.comments_added == 1

    posts = (await session.execute(select(Post))).scalars().all()
    comments = (await session.execute(select(Comment))).scalars().all()
    assert len(posts) == 1
    assert len(comments) == 3


async def test_an_unsupported_platform_is_refused(session):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as caught:
        await _capture(session, platform="linkedin")

    assert caught.value.status_code == 400


async def test_the_comment_count_is_capped(session):
    """A malformed payload must not enqueue thousands of classification calls."""
    from src.api.extension_router import MAX_COMMENTS

    many = [
        {"platform_comment_id": f"c{i}", "text": f"t{i}", "author_name": "X"}
        for i in range(MAX_COMMENTS + 50)
    ]
    result = await _capture(session, comments=many)

    assert result.comments_added == MAX_COMMENTS

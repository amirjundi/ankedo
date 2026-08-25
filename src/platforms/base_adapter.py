"""Adapter interface shared by every platform.

Selectors are kept in a per-platform `SELECTORS` mapping rather than inline, for two
reasons: a layout change becomes a one-file edit, and `detect_ui_change` can check the
whole set generically. When selectors do break, the vision agent re-derives the page
and proposes replacements against these same keys.
"""
from __future__ import annotations

import abc
from typing import Any

import structlog
from playwright.async_api import Page

log = structlog.get_logger()


class SelectorsBroken(RuntimeError):
    """Core selectors did not match — the layout has changed.

    Raised rather than returning empty, because "no posts found" and "the page is not
    what we expect" need completely different responses. Silently returning [] would
    look like a quiet account with nothing to monitor.
    """

    def __init__(self, platform: str, missing: list[str], url: str):
        self.platform, self.missing, self.url = platform, missing, url
        super().__init__(f"{platform}: selectors did not match {missing} at {url}")


class PlatformAdapter(abc.ABC):
    """Abstract interface for all social media platform scrapers."""

    platform: str = "unknown"
    # Selector keys that must match on a healthy page. Checked by detect_ui_change.
    SELECTORS: dict[str, str] = {}
    CRITICAL_SELECTORS: tuple[str, ...] = ()

    @abc.abstractmethod
    async def fetch_new_posts(
        self, page: Page, account_url: str, max_posts: int = 10
    ) -> list[dict[str, Any]]:
        """Fetch recent posts from a target account.

        Returns dicts carrying platform_post_id, url, content_text, media_urls,
        author_name.
        """

    @abc.abstractmethod
    async def fetch_comments(
        self, page: Page, post_url: str, max_comments: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch comments for a post.

        Also returns `comments_seen`, the denominator the platform needs to compute
        hate density (AGENT_CONTRACT amendment §1) — a count of what was looked at,
        not just what matched.
        """

    @abc.abstractmethod
    async def take_screenshot(self, page: Page, item_url: str, mode: str, output_path: str) -> bool:
        """Navigate, frame the item, and capture evidence."""

    async def detect_ui_change(self, page: Page) -> list[str]:
        """Return the critical selectors that failed to match.

        An empty list means the page looks as expected. A non-empty list is the
        trigger for the vision fallback (T091).
        """
        missing = []
        for key in self.CRITICAL_SELECTORS:
            selector = self.SELECTORS.get(key)
            if not selector:
                continue
            if await page.query_selector(selector) is None:
                missing.append(key)
        if missing:
            log.warning(
                "Platform UI changed", platform=self.platform, missing=missing, url=page.url
            )
        return missing

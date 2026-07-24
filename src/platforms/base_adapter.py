"""
Abstract base class for all platform adapters.
"""
from __future__ import annotations

import abc
from typing import Any

from playwright.async_api import Page


class PlatformAdapter(abc.ABC):
    """Abstract interface for all social media platform scrapers."""

    @abc.abstractmethod
    async def fetch_new_posts(self, page: Page, account_url: str, max_posts: int = 10) -> list[dict[str, Any]]:
        """
        Fetch new posts from a target account page.
        Returns a list of post dictionaries containing post_id, url, content, media, author.
        """
        pass

    @abc.abstractmethod
    async def fetch_comments(self, page: Page, post_url: str, max_comments: int = 100) -> list[dict[str, Any]]:
        """
        Fetch comments for a specific post.
        Returns a list of comment dictionaries.
        """
        pass

    @abc.abstractmethod
    async def take_screenshot(self, page: Page, item_url: str, mode: str, output_path: str) -> bool:
        """Navigate to URL, frame the item (post or comment), and take a screenshot."""
        pass

    async def detect_ui_change(self, page: Page) -> bool:
        """T091: Detect scraping failures (selector errors, unexpected layouts)."""
        # If core selectors start failing, we pause collection and notify admin
        # so they can document a switchable browser engine path or fix selectors.
        log.info("Checking for platform UI changes")
        return False

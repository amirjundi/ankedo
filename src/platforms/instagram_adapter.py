"""
Instagram platform adapter.
"""
from __future__ import annotations

from typing import Any
import structlog
from playwright.async_api import Page

from src.platforms.base_adapter import PlatformAdapter

log = structlog.get_logger()


class InstagramAdapter(PlatformAdapter):
    """Adapter for Instagram scraping using Camoufox/Playwright."""

    async def fetch_new_posts(self, page: Page, account_url: str, max_posts: int = 10) -> list[dict[str, Any]]:
        """Fetch posts from an Instagram profile."""
        log.info("Fetching Instagram posts", url=account_url)
        await page.goto(account_url, wait_until="domcontentloaded")
        return []

    async def fetch_comments(self, page: Page, post_url: str, max_comments: int = 100) -> list[dict[str, Any]]:
        """Fetch comments for an Instagram post."""
        log.info("Fetching Instagram comments", url=post_url)
        await page.goto(post_url, wait_until="domcontentloaded")
        return []

    async def take_screenshot(self, page: Page, item_url: str, mode: str, output_path: str) -> bool:
        """Frame the Instagram post/comment and take a screenshot."""
        log.info("Taking Instagram screenshot", url=item_url, mode=mode)
        await page.goto(item_url, wait_until="networkidle")
        
        # Stub: Instagram specific framing logic
        if mode == "comment":
            log.debug("Highlighting target comment on Instagram")
            
        await page.screenshot(path=output_path, full_page=True)
        return True

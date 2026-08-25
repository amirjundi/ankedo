"""Instagram adapter.

First platform implemented, because the Telegram bot already manages an Instagram
allow-list, so it is the shortest path to a live loop.

**The selectors below are unverified against live Instagram.** They follow the
structures Instagram has used, but the site changes its markup often and obfuscates
class names, so these must be checked against real pages before any production run.
That is exactly why they live in one mapping and why `detect_ui_change` exists — the
expected failure mode is "selectors stopped matching", handled by falling back to the
vision agent, not by silently collecting nothing.
"""
from __future__ import annotations

from typing import Any

import structlog
from playwright.async_api import Page

from src.browsers.cursor import HumanCursor
from src.platforms.base_adapter import PlatformAdapter, SelectorsBroken

log = structlog.get_logger()


class InstagramAdapter(PlatformAdapter):
    platform = "instagram"

    SELECTORS = {
        # A profile grid renders article links to individual posts.
        "profile_posts": 'main article a[href*="/p/"], main a[href*="/reel/"]',
        "post_container": "article",
        "post_caption": 'article h1, article [data-testid="post-comment-root"] span',
        "comment_list": "article ul ul",
        "comment_item": "article ul ul li",
        "comment_text": "span[dir='auto']",
        "comment_author": 'a[role="link"]',
        "load_more_comments": 'button svg[aria-label="Load more comments"]',
        "login_wall": 'input[name="username"]',
    }
    # If these are absent the page is not what we think it is.
    CRITICAL_SELECTORS = ("post_container",)

    async def fetch_new_posts(
        self, page: Page, account_url: str, max_posts: int = 10
    ) -> list[dict[str, Any]]:
        await page.goto(account_url, wait_until="domcontentloaded")

        if await page.query_selector(self.SELECTORS["login_wall"]):
            raise SelectorsBroken(self.platform, ["session lost — login wall"], account_url)

        missing = await self.detect_ui_change(page)
        if missing:
            raise SelectorsBroken(self.platform, missing, account_url)

        links = await page.query_selector_all(self.SELECTORS["profile_posts"])
        posts: list[dict[str, Any]] = []
        seen: set[str] = set()

        for link in links:
            if len(posts) >= max_posts:
                break
            href = await link.get_attribute("href")
            if not href:
                continue
            shortcode = _shortcode(href)
            if not shortcode or shortcode in seen:
                continue
            seen.add(shortcode)
            posts.append(
                {
                    "platform_post_id": shortcode,
                    "url": f"https://www.instagram.com{href}",
                    "content_text": None,  # filled when the post itself is opened
                    "media_urls": [],
                    "author_name": _handle(account_url),
                }
            )

        log.info("Instagram posts found", count=len(posts), url=account_url)
        return posts

    async def fetch_comments(
        self, page: Page, post_url: str, max_comments: int = 100
    ) -> list[dict[str, Any]]:
        await page.goto(post_url, wait_until="domcontentloaded")

        missing = await self.detect_ui_change(page)
        if missing:
            raise SelectorsBroken(self.platform, missing, post_url)

        cursor = HumanCursor(page)

        # Instagram paginates comments behind a button; keep expanding until the
        # target count or the button disappears.
        for _ in range(10):
            existing = await page.query_selector_all(self.SELECTORS["comment_item"])
            if len(existing) >= max_comments:
                break
            if not await cursor.click_selector(self.SELECTORS["load_more_comments"]):
                break
            await page.wait_for_timeout(1200)

        comments: list[dict[str, Any]] = []
        for index, item in enumerate(
            await page.query_selector_all(self.SELECTORS["comment_item"])
        ):
            if len(comments) >= max_comments:
                break
            text_el = await item.query_selector(self.SELECTORS["comment_text"])
            author_el = await item.query_selector(self.SELECTORS["comment_author"])
            text = (await text_el.inner_text()).strip() if text_el else ""
            if not text:
                continue
            comments.append(
                {
                    "platform_comment_id": f"{_shortcode(post_url)}-{index}",
                    "text": text,
                    "author_name": (await author_el.inner_text()).strip() if author_el else None,
                }
            )

        log.info("Instagram comments collected", count=len(comments), url=post_url)
        return comments

    async def take_screenshot(
        self, page: Page, item_url: str, mode: str, output_path: str
    ) -> bool:
        await page.goto(item_url, wait_until="networkidle")
        if mode == "comment":
            # Frame the comment thread rather than the whole page, so the evidence
            # shows the comment in the context that makes it hateful.
            element = await page.query_selector(self.SELECTORS["comment_list"])
            if element:
                await element.screenshot(path=output_path)
                return True
        await page.screenshot(path=output_path, full_page=True)
        return True


def _shortcode(href: str) -> str | None:
    parts = [p for p in href.split("?")[0].split("/") if p]
    for marker in ("p", "reel"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    return None


def _handle(account_url: str) -> str | None:
    parts = [p for p in account_url.split("?")[0].split("/") if p]
    return parts[-1] if parts else None

"""Facebook adapter.

**Selectors unverified against live Facebook.** Facebook randomises class names per
session and varies markup by A/B cohort, so `role`/`aria` attributes are used wherever
possible — they track accessibility semantics rather than styling, and survive
redesigns far better than class selectors.

Facebook is the hardest of the three: heaviest bot detection, aggressive checkpoints,
and comment threads that lazy-load behind several "view more" variants.
"""
from __future__ import annotations

from typing import Any

import structlog
from playwright.async_api import Page

from src.browsers.cursor import HumanCursor
from src.platforms.base_adapter import PlatformAdapter, SelectorsBroken

log = structlog.get_logger()


class FacebookAdapter(PlatformAdapter):
    platform = "facebook"

    SELECTORS = {
        "feed": '[role="feed"], [role="main"]',
        "post_container": '[role="article"]',
        "post_permalink": 'a[href*="/posts/"], a[href*="story_fbid"], a[href*="/videos/"]',
        "post_text": '[data-ad-preview="message"], [data-ad-comet-preview="message"]',
        "comment_item": '[role="article"] [role="article"]',
        "comment_text": 'div[dir="auto"]',
        "comment_author": 'a[role="link"] span',
        "view_more_comments": 'div[role="button"]:has-text("more comment")',
        "see_more_text": 'div[role="button"]:has-text("See more")',
        "checkpoint": 'form[action*="checkpoint"], #checkpointSubmitButton',
        "login_wall": 'input[name="pass"]',
    }
    CRITICAL_SELECTORS = ("post_container",)

    async def fetch_new_posts(
        self, page: Page, account_url: str, max_posts: int = 10
    ) -> list[dict[str, Any]]:
        await page.goto(account_url, wait_until="domcontentloaded")
        await self._guard(page, account_url)

        cursor = HumanCursor(page)
        posts: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Facebook renders lazily; scroll until enough posts appear or it stops growing.
        for _ in range(8):
            containers = await page.query_selector_all(self.SELECTORS["post_container"])
            for container in containers:
                if len(posts) >= max_posts:
                    break
                link = await container.query_selector(self.SELECTORS["post_permalink"])
                href = await link.get_attribute("href") if link else None
                post_id = _post_id(href) if href else None
                if not post_id or post_id in seen:
                    continue
                seen.add(post_id)

                text_el = await container.query_selector(self.SELECTORS["post_text"])
                posts.append(
                    {
                        "platform_post_id": post_id,
                        "url": href.split("?")[0] if href.startswith("http") else f"https://www.facebook.com{href}",
                        "content_text": (await text_el.inner_text()).strip() if text_el else None,
                        "media_urls": [],
                        "author_name": None,
                    }
                )
            if len(posts) >= max_posts:
                break
            await page.mouse.wheel(0, 900)
            await page.wait_for_timeout(1500)

        log.info("Facebook posts found", count=len(posts), url=account_url)
        return posts

    async def fetch_comments(
        self, page: Page, post_url: str, max_comments: int = 100
    ) -> list[dict[str, Any]]:
        await page.goto(post_url, wait_until="domcontentloaded")
        await self._guard(page, post_url)

        cursor = HumanCursor(page)
        for _ in range(12):
            items = await page.query_selector_all(self.SELECTORS["comment_item"])
            if len(items) >= max_comments:
                break
            if not await cursor.click_selector(self.SELECTORS["view_more_comments"]):
                break
            await page.wait_for_timeout(1500)

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
                    "platform_comment_id": f"{_post_id(post_url) or 'post'}-{index}",
                    "text": text,
                    "author_name": (await author_el.inner_text()).strip() if author_el else None,
                }
            )

        log.info("Facebook comments collected", count=len(comments), url=post_url)
        return comments

    async def take_screenshot(
        self, page: Page, item_url: str, mode: str, output_path: str
    ) -> bool:
        await page.goto(item_url, wait_until="networkidle")
        if mode == "comment":
            element = await page.query_selector(self.SELECTORS["comment_item"])
            if element:
                await element.screenshot(path=output_path)
                return True
        await page.screenshot(path=output_path, full_page=True)
        return True

    async def _guard(self, page: Page, url: str) -> None:
        """Distinguish a checkpoint from a layout change — they need different responses."""
        if await page.query_selector(self.SELECTORS["checkpoint"]):
            raise SelectorsBroken(self.platform, ["checkpoint — needs a human"], url)
        if await page.query_selector(self.SELECTORS["login_wall"]):
            raise SelectorsBroken(self.platform, ["session lost — login wall"], url)
        missing = await self.detect_ui_change(page)
        if missing:
            raise SelectorsBroken(self.platform, missing, url)


def _post_id(href: str | None) -> str | None:
    if not href:
        return None
    clean = href.split("?")[0].rstrip("/")
    for marker in ("/posts/", "/videos/", "/reel/"):
        if marker in clean:
            return clean.split(marker)[-1].split("/")[0]
    if "story_fbid=" in href:
        return href.split("story_fbid=")[-1].split("&")[0]
    return None

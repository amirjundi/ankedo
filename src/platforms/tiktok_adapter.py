"""TikTok adapter.

**Selectors unverified against live TikTok.** TikTok uses stable `data-e2e` attributes
for its own test automation, which are far more durable than class names — those are
preferred here.

Two things make TikTok different from the other two platforms:

* **The message is usually spoken, not written.** Post text is a caption; the hate
  speech is often in the audio. Until transcription exists, caption-only collection
  systematically under-reads this platform, and that limitation should be visible in
  coverage reporting rather than mistaken for a quiet source.
* Comments load by scrolling a virtualised list, so the collector scrolls the panel
  rather than clicking a "load more" control.
"""
from __future__ import annotations

from typing import Any

import structlog
from playwright.async_api import Page

from src.platforms.base_adapter import PlatformAdapter, SelectorsBroken

log = structlog.get_logger()


class TikTokAdapter(PlatformAdapter):
    platform = "tiktok"

    SELECTORS = {
        "profile_videos": '[data-e2e="user-post-item"] a',
        "post_container": '[data-e2e="browse-video"], [data-e2e="user-post-item"]',
        "post_caption": '[data-e2e="browse-video-desc"], [data-e2e="video-desc"]',
        "comment_list": '[data-e2e="comment-list"]',
        "comment_item": '[data-e2e="comment-level-1"]',
        "comment_text": '[data-e2e="comment-level-1"] p, span[data-e2e="comment-text"]',
        "comment_author": '[data-e2e="comment-username-1"]',
        "captcha": "#captcha-verify-container, .captcha_verify_container",
        "login_wall": '[data-e2e="login-button"]',
    }
    CRITICAL_SELECTORS = ("post_container",)

    async def fetch_new_posts(
        self, page: Page, account_url: str, max_posts: int = 10
    ) -> list[dict[str, Any]]:
        await page.goto(account_url, wait_until="domcontentloaded")
        await self._guard(page, account_url)

        posts: list[dict[str, Any]] = []
        seen: set[str] = set()

        for link in await page.query_selector_all(self.SELECTORS["profile_videos"]):
            if len(posts) >= max_posts:
                break
            href = await link.get_attribute("href")
            video_id = _video_id(href)
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            posts.append(
                {
                    "platform_post_id": video_id,
                    "url": href,
                    "content_text": None,
                    "media_urls": [href],
                    "author_name": _handle(account_url),
                    # Flag that the spoken content was not read, so coverage
                    # reporting does not treat this as fully scanned.
                    "audio_untranscribed": True,
                }
            )

        log.info("TikTok posts found", count=len(posts), url=account_url)
        return posts

    async def fetch_comments(
        self, page: Page, post_url: str, max_comments: int = 100
    ) -> list[dict[str, Any]]:
        await page.goto(post_url, wait_until="domcontentloaded")
        await self._guard(page, post_url)

        # Virtualised list: scroll the panel rather than clicking a control.
        for _ in range(12):
            items = await page.query_selector_all(self.SELECTORS["comment_item"])
            if len(items) >= max_comments:
                break
            panel = await page.query_selector(self.SELECTORS["comment_list"])
            if panel is None:
                break
            await panel.hover()
            await page.mouse.wheel(0, 800)
            await page.wait_for_timeout(1200)

        comments: list[dict[str, Any]] = []
        for index, item in enumerate(
            await page.query_selector_all(self.SELECTORS["comment_item"])
        ):
            if len(comments) >= max_comments:
                break
            text_el = await item.query_selector("p, span")
            author_el = await item.query_selector(self.SELECTORS["comment_author"])
            text = (await text_el.inner_text()).strip() if text_el else ""
            if not text:
                continue
            comments.append(
                {
                    "platform_comment_id": f"{_video_id(post_url) or 'video'}-{index}",
                    "text": text,
                    "author_name": (await author_el.inner_text()).strip() if author_el else None,
                }
            )

        log.info("TikTok comments collected", count=len(comments), url=post_url)
        return comments

    async def take_screenshot(
        self, page: Page, item_url: str, mode: str, output_path: str
    ) -> bool:
        await page.goto(item_url, wait_until="networkidle")
        if mode == "comment":
            element = await page.query_selector(self.SELECTORS["comment_list"])
            if element:
                await element.screenshot(path=output_path)
                return True
        await page.screenshot(path=output_path, full_page=True)
        return True

    async def _guard(self, page: Page, url: str) -> None:
        if await page.query_selector(self.SELECTORS["captcha"]):
            raise SelectorsBroken(self.platform, ["captcha — needs a human"], url)
        missing = await self.detect_ui_change(page)
        if missing:
            raise SelectorsBroken(self.platform, missing, url)


def _video_id(href: str | None) -> str | None:
    if not href:
        return None
    clean = href.split("?")[0].rstrip("/")
    return clean.split("/video/")[-1] if "/video/" in clean else None


def _handle(account_url: str) -> str | None:
    for part in account_url.split("/"):
        if part.startswith("@"):
            return part
    return None

"""
Screenshot Worker - Captures platform-appropriate screenshots using Camoufox.
"""
from __future__ import annotations

import structlog
from playwright.async_api import Page

from src.browsers.camoufox_worker import CamoufoxWorker
from src.platforms.base_adapter import PlatformAdapter

log = structlog.get_logger()


class ScreenshotWorker:
    """Uses a browser worker and platform adapter to take framed screenshots."""

    def __init__(self, browser_worker: CamoufoxWorker, adapter: PlatformAdapter):
        self.browser_worker = browser_worker
        self.adapter = adapter

    async def capture(self, item_url: str, mode: str, output_path: str) -> bool:
        """
        Capture screenshot for evidence.
        mode: 'post' (captures full post) or 'comment' (captures parent post + flagged comment)
        """
        log.info("Capturing screenshot", item_url=item_url, mode=mode)
        try:
            return await self.adapter.take_screenshot(
                page=self.browser_worker.page,
                item_url=item_url,
                mode=mode,
                output_path=output_path
            )
        except Exception as e:
            log.exception("Screenshot capture failed", error=str(e), item_url=item_url)
            return False

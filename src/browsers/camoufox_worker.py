"""
Camoufox-based browser worker with persistent fingerprints, human-like pacing,
natural browsing behavior, and CAPTCHA handling.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any

import structlog
from camoufox.async_api import AsyncCamoufox
from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from src.core.settings import get_settings

log = structlog.get_logger()


class CamoufoxWorker:
    """Worker managing an undetected browser session for a specific account."""

    def __init__(self, platform: str, account_id: str, proxy: str | None = None):
        self.platform = platform
        self.account_id = account_id
        self.proxy = proxy
        self.settings = get_settings()
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._playwright = None

    async def start(self) -> None:
        """Launch the Camoufox browser and load the persistent session."""
        log.info("Starting Camoufox worker", account_id=self.account_id, platform=self.platform)
        self._playwright = await async_playwright().start()
        
        # In a real implementation, we would use the fingerprint seed to generate
        # consistent headers, canvas, etc. Camoufox handles most of this automatically.
        proxy_dict = {"server": self.proxy} if self.proxy else None
        
        # Headed mode matters operationally, not just for debugging: a human taking
        # over a CAPTCHA needs a window to work in.
        self._browser = await AsyncCamoufox(
            playwright=self._playwright,
            headless=self.settings.browser_headless,
            proxy=proxy_dict,
            # Pass a consistent path for session persistence
            user_data_dir=f"./sessions/{self.account_id}"
        )
        self._context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        self._page = await self._context.new_page()

    async def stop(self) -> None:
        """Close the browser safely."""
        log.info("Stopping Camoufox worker", account_id=self.account_id)
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def pacing_delay(self) -> None:
        """Human-like randomized pacing (Gaussian-distributed delay)."""
        mu = (self.settings.pacing_min_delay_seconds + self.settings.pacing_max_delay_seconds) / 2
        sigma = (self.settings.pacing_max_delay_seconds - self.settings.pacing_min_delay_seconds) / 4
        delay = random.gauss(mu, sigma)
        delay = max(self.settings.pacing_min_delay_seconds, min(delay, self.settings.pacing_max_delay_seconds))
        await asyncio.sleep(delay)

    async def natural_scroll(self, page: Page) -> None:
        """Simulate a human scrolling."""
        scroll_amount = random.randint(300, 800)
        await page.mouse.wheel(0, scroll_amount)
        await self.pacing_delay()

    async def check_captcha(self, page: Page) -> bool:
        """Detect CAPTCHAs or blocks."""
        # Stub logic: platforms have different CAPTCHA selectors.
        # e.g., Facebook might show a specific checkpoint URL or element.
        content = await page.content()
        if "checkpoint" in page.url or "captcha" in content.lower():
            log.warning("CAPTCHA/Block detected", account_id=self.account_id)
            # Notification logic to admin would be triggered here
            return True
        return False

    async def navigate(self, url: str) -> bool:
        """Navigate to a URL with natural behavior and checks."""
        if not self._page:
            raise RuntimeError("Browser not started")
            
        await self._page.goto(url, wait_until="networkidle")
        await self.pacing_delay()
        
        if await self.check_captcha(self._page):
            return False
            
        return True

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("Browser not started")
        return self._page

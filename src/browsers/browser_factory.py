"""
Browser Factory — instantiates the correct browser engine.
"""
from __future__ import annotations

from src.browsers.camoufox_worker import CamoufoxWorker


class BrowserFactory:
    """Creates browser workers based on platform requirements."""

    @staticmethod
    def create_worker(platform: str, account_id: str, proxy: str | None = None) -> CamoufoxWorker:
        """
        Create and return a worker.
        For AnkEdo, Camoufox is the default engine for all platforms (FB, TikTok, IG).
        """
        # In the future, undetected-chromedriver could be added here if needed.
        return CamoufoxWorker(platform=platform, account_id=account_id, proxy=proxy)

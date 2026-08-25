"""
Proxy Manager — enforces rules against datacenter IPs, uses residential proxies.
"""
from __future__ import annotations

import structlog

from src.core.settings import get_settings

log = structlog.get_logger()


class ProxyManager:
    """Manages residential proxies and prevents datacenter IP usage."""

    def __init__(self):
        self.settings = get_settings()
        # copy — get_settings() is lru_cached, mutating the list would corrupt it globally
        self.proxies = list(self.settings.proxy_list)
        self._current_index = 0

    def get_proxy(self) -> str | None:
        """Return the next residential proxy, or None for home WiFi."""
        if not self.proxies:
            return None
            
        proxy = self.proxies[self._current_index]
        self._current_index = (self._current_index + 1) % len(self.proxies)
        return proxy

    def validate_proxy(self, proxy: str) -> bool:
        """
        Validate that a proxy is not a datacenter IP.
        In a full implementation, this might call an IP-info API.
        """
        # ponytail: no datacenter-IP lookup yet, accepts anything non-empty.
        # Add an ipinfo/ip-api check when residential proxies are actually provisioned.
        return bool(proxy)

    def mark_failed(self, proxy: str, reason: str) -> None:
        """Drop a proxy from rotation after a block or connection failure."""
        # ponytail: in-memory rotation, forgotten on restart. Persist to a proxies
        # table if proxy health needs to survive a restart.
        if proxy in self.proxies:
            self.proxies.remove(proxy)
            self._current_index = 0
        log.warning("Proxy marked as failed", proxy=proxy, reason=reason)

    async def detect_home_ip_flagging(self, platform: str) -> bool:
        """T090: Detect if the home IP is rejected platform-wide."""
        # Stub: if all workers on a platform report IP blocks
        # we pause monitoring on that platform and notify admin to switch proxies.
        home_ip_blocked = False # Stub
        if home_ip_blocked:
            log.critical("Home IP flagged by platform", platform=platform)
            # Dispatch notification (stub)
            return True
        return False

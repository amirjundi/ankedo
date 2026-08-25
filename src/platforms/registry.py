"""Platform adapter discovery.

Built-in adapters are registered here; third-party ones can register themselves
through the `ankedo.adapters` entry-point group, so adding a platform (X, YouTube,
Telegram channels, a local Iraqi forum) is a dropped-in package rather than an edit
to core.

Deliberately *not* extended to classifiers: a pluggable classifier would break the
reproducibility FR-CL-13 requires for eval-gating and audit.
"""
from __future__ import annotations

from importlib.metadata import entry_points

import structlog

from src.platforms.base_adapter import PlatformAdapter
from src.platforms.facebook_adapter import FacebookAdapter
from src.platforms.instagram_adapter import InstagramAdapter
from src.platforms.tiktok_adapter import TikTokAdapter

log = structlog.get_logger()

_BUILTIN: dict[str, type[PlatformAdapter]] = {
    "facebook": FacebookAdapter,
    "instagram": InstagramAdapter,
    "tiktok": TikTokAdapter,
}

_cache: dict[str, type[PlatformAdapter]] | None = None


def available() -> dict[str, type[PlatformAdapter]]:
    """All adapters, built-in plus anything installed via entry points."""
    global _cache
    if _cache is not None:
        return _cache

    found = dict(_BUILTIN)
    try:
        for entry in entry_points(group="ankedo.adapters"):
            try:
                adapter = entry.load()
            except Exception as exc:  # a broken plugin must not stop the agent
                log.error("Adapter plugin failed to load", name=entry.name, error=str(exc))
                continue
            if not issubclass(adapter, PlatformAdapter):
                log.error("Adapter plugin is not a PlatformAdapter", name=entry.name)
                continue
            found[entry.name] = adapter
            log.info("Loaded adapter plugin", platform=entry.name)
    except Exception as exc:
        log.warning("Entry-point discovery failed", error=str(exc))

    _cache = found
    return found


def get_adapter(platform: str) -> PlatformAdapter:
    """Instantiate the adapter for a platform."""
    adapters = available()
    key = platform.lower().strip()
    if key not in adapters:
        raise KeyError(f"no adapter for {platform!r} — available: {sorted(adapters)}")
    return adapters[key]()


def reset_cache() -> None:
    global _cache
    _cache = None

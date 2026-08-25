"""Selector failure routing.

The distinction being tested: *why* selectors broke decides what happens next. A
layout change is recoverable by the agent; a CAPTCHA is not, and attempting one is
itself a bot signal.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from src.core.settings import get_settings
from src.platforms.base_adapter import PlatformAdapter, SelectorsBroken


class FakeAdapter(PlatformAdapter):
    platform = "instagram"
    SELECTORS = {"post_container": "article"}
    CRITICAL_SELECTORS = ("post_container",)

    def __init__(self, raises: SelectorsBroken | None = None, items=None):
        self.raises = raises
        self.items = items or []

    async def fetch_new_posts(self, page, account_url, max_posts=10):
        return []

    async def fetch_comments(self, page, post_url, max_comments=100):
        if self.raises:
            raise self.raises
        return self.items

    async def take_screenshot(self, page, item_url, mode, output_path):
        return True


class FakeVision:
    def __init__(self, extracted="one\ntwo", selectors="post_container: main article"):
        self.extracted = extracted
        self.selectors = selectors
        self.calls = 0

    async def run(self, goal, max_steps=None):
        self.calls += 1
        payload = self.selectors if "propose CSS" in goal else self.extracted
        return {"status": "done", "extracted": payload, "steps": []}


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import get_session, init_db

    await init_db()
    async with get_session() as s:
        yield s

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


def _collector(session, adapter, vision=None, monkeypatch=None):
    from src.browsers import resilient_collector as module

    collector = module.ResilientCollector(session, page=object(), adapter=adapter)
    if vision is not None:
        monkeypatch.setattr(module, "VisionAgent", lambda *a, **k: vision)
    return collector


async def test_working_selectors_never_invoke_vision(session, monkeypatch):
    vision = FakeVision()
    adapter = FakeAdapter(items=[{"text": "hello"}])
    collector = _collector(session, adapter, vision, monkeypatch)

    outcome = await collector.fetch_comments("https://instagram.com/p/abc/")

    assert outcome.items == [{"text": "hello"}]
    assert outcome.used_vision is False
    assert vision.calls == 0, "the cheap path must not pay for vision"


async def test_layout_change_recovers_via_vision_and_proposes_selectors(session, monkeypatch):
    vision = FakeVision()
    adapter = FakeAdapter(
        raises=SelectorsBroken("instagram", ["post_container"], "https://instagram.com/p/abc/")
    )
    collector = _collector(session, adapter, vision, monkeypatch)

    outcome = await collector.fetch_comments("https://instagram.com/p/abc/")

    assert outcome.used_vision is True
    assert [i["text"] for i in outcome.items] == ["one", "two"], "the run still completes"
    assert outcome.proposed_selectors == {"post_container": "main article"}
    assert outcome.needs_human is False


@pytest.mark.parametrize(
    "reason", ["captcha — needs a human", "checkpoint — needs a human", "session lost — login wall"]
)
async def test_human_problems_are_escalated_not_attempted(session, monkeypatch, reason):
    vision = FakeVision()
    adapter = FakeAdapter(raises=SelectorsBroken("facebook", [reason], "https://facebook.com/x"))
    collector = _collector(session, adapter, vision, monkeypatch)

    outcome = await collector.fetch_comments("https://facebook.com/x")

    assert outcome.needs_human is True
    assert outcome.used_vision is False
    assert vision.calls == 0, "attempting a CAPTCHA is itself a bot signal"


async def test_escalation_creates_a_notification(session, monkeypatch):
    from sqlalchemy import select

    from src.models.agent_notification import AgentNotification

    adapter = FakeAdapter(raises=SelectorsBroken("facebook", ["captcha"], "https://facebook.com/x"))
    await _collector(session, adapter, FakeVision(), monkeypatch).fetch_comments("https://facebook.com/x")

    notifications = (await session.execute(select(AgentNotification))).scalars().all()
    assert [n.notification_type for n in notifications] == ["HumanInterventionRequired"]
    assert notifications[0].urgency == "Critical"


async def test_proposed_selectors_are_never_auto_applied(session, monkeypatch):
    """A model-invented selector becomes part of what the system watches with."""
    adapter = FakeAdapter(
        raises=SelectorsBroken("instagram", ["post_container"], "https://instagram.com/p/abc/")
    )
    before = dict(adapter.SELECTORS)

    await _collector(session, adapter, FakeVision(), monkeypatch).fetch_comments(
        "https://instagram.com/p/abc/"
    )

    assert adapter.SELECTORS == before, "proposals go to a human, they do not self-apply"


# ------------------------------------------------------------------ registry


def test_registry_exposes_the_three_platforms():
    from src.platforms.registry import available

    assert {"facebook", "instagram", "tiktok"} <= set(available())


def test_unknown_platform_names_what_is_available():
    from src.platforms.registry import get_adapter

    with pytest.raises(KeyError, match="available"):
        get_adapter("myspace")


def test_adapters_declare_their_critical_selectors():
    """detect_ui_change is inert for an adapter that declares none."""
    from src.platforms.registry import available

    for name, adapter in available().items():
        assert adapter.CRITICAL_SELECTORS, f"{name} has no critical selectors"
        for key in adapter.CRITICAL_SELECTORS:
            assert key in adapter.SELECTORS, f"{name}: {key} missing from SELECTORS"

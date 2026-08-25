"""Vision loop control flow, with the model and the page both faked.

The behaviours pinned here are the ones that cost money or safety when wrong:
coordinate scaling, the step ceiling, the domain allowlist, and handing a CAPTCHA to
a human instead of attempting it.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from src.browsers.vision_agent import Action, VisionAgent, VisionBlocked, VisionStep
from src.core.settings import get_settings


class FakeMouse:
    def __init__(self):
        self.moves: list[tuple[float, float]] = []
        self.clicks = 0
        self.wheel_calls: list[int] = []

    async def move(self, x, y):
        self.moves.append((x, y))

    async def down(self):
        pass

    async def up(self):
        self.clicks += 1

    async def wheel(self, dx, dy):
        self.wheel_calls.append(dy)


class FakePage:
    def __init__(self, url="https://www.instagram.com/somepage/", viewport=None):
        self.url = url
        self.mouse = FakeMouse()
        self.viewport_size = viewport or {"width": 1280, "height": 800}
        self.keyboard = self
        self.typed: list[str] = []
        self.navigations: list[str] = []

    async def screenshot(self, **kwargs):
        return b"fake-png"

    async def inner_text(self, selector):
        return "some page text"

    async def type(self, text, delay=0):
        self.typed.append(text)

    async def goto(self, url, **kwargs):
        self.navigations.append(url)
        self.url = url

    async def wait_for_timeout(self, ms):
        pass


class FakeLLM:
    """Replays a scripted sequence of vision steps."""

    def __init__(self, steps: list[VisionStep]):
        self.steps = list(steps)
        self.calls = 0

    async def generate(self, **kwargs):
        self.calls += 1
        if not self.steps:
            raise AssertionError("vision agent asked for more steps than scripted")
        return self.steps.pop(0)


@pytest.fixture(autouse=True)
def fast_cursor(monkeypatch):
    """Strip the human-pacing sleeps so tests do not wait on them."""
    async def no_sleep(_):
        return None

    monkeypatch.setattr("src.browsers.cursor.asyncio.sleep", no_sleep)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _agent(page, steps):
    return VisionAgent(session=None, page=page, llm=FakeLLM(steps))


async def test_completes_and_returns_extracted_content():
    page = FakePage()
    agent = _agent(page, [VisionStep(action=Action.DONE, extracted="the comments", reason="found")])

    result = await agent.run("read the comments")

    assert result["status"] == "done"
    assert result["extracted"] == "the comments"


async def test_normalised_coordinates_scale_to_the_viewport():
    """The 0-1000 grid is the classic source of clicks landing near, not on, target."""
    page = FakePage(viewport={"width": 1000, "height": 500})
    agent = _agent(
        page,
        [
            VisionStep(action=Action.CLICK, x=500, y=1000, reason="centre-bottom"),
            VisionStep(action=Action.DONE, reason="done"),
        ],
    )

    await agent.run("click something")

    # x: 500/1000 * 1000 = 500 ; y: 1000/1000 * 500 = 500
    assert page.mouse.moves[-1] == (500.0, 500.0)
    assert page.mouse.clicks == 1


async def test_captcha_is_handed_to_a_human_not_attempted():
    page = FakePage()
    agent = _agent(page, [VisionStep(action=Action.BLOCKED, reason="a CAPTCHA is on screen")])

    with pytest.raises(VisionBlocked, match="CAPTCHA"):
        await agent.run("log in")

    assert page.mouse.clicks == 0, "must not click at a CAPTCHA"


async def test_step_ceiling_stops_a_loop_that_never_converges():
    """FR-AG-7: cost ceilings are enforced in code, not left to the model."""
    page = FakePage()
    steps = [VisionStep(action=Action.SCROLL, scroll_amount=400, reason="looking") for _ in range(10)]
    agent = _agent(page, steps)

    with pytest.raises(VisionBlocked, match="no resolution within 3 steps"):
        await agent.run("find something that isn't there", max_steps=3)

    assert agent.llm.calls == 3, "must stop at the ceiling, not keep spending"


async def test_navigation_off_the_allowlist_is_refused(monkeypatch):
    monkeypatch.setenv("VISION_DOMAIN_ALLOWLIST", "instagram.com")
    get_settings.cache_clear()

    page = FakePage()
    agent = _agent(
        page,
        [VisionStep(action=Action.NAVIGATE, text="https://evil.example/steal", reason="go")],
    )

    with pytest.raises(VisionBlocked, match="allowlist"):
        await agent.run("navigate away")

    assert page.navigations == [], "the guardrail must block before navigating"


async def test_starting_on_a_disallowed_domain_is_refused(monkeypatch):
    monkeypatch.setenv("VISION_DOMAIN_ALLOWLIST", "instagram.com")
    get_settings.cache_clear()

    agent = _agent(FakePage(url="https://elsewhere.example/"), [])
    with pytest.raises(VisionBlocked, match="allowlist"):
        await agent.run("anything")


async def test_every_step_is_logged_with_its_reason():
    """NFR-AU-2 — agent decisions must retain the trace that produced them."""
    page = FakePage()
    agent = _agent(
        page,
        [
            VisionStep(action=Action.SCROLL, scroll_amount=600, reason="comments are below"),
            VisionStep(action=Action.DONE, extracted="x", reason="got them"),
        ],
    )

    result = await agent.run("read comments")

    assert [s["reason"] for s in result["steps"]] == ["comments are below", "got them"]
    assert all("url" in s and "action" in s for s in result["steps"])

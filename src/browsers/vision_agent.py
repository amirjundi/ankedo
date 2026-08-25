"""Vision-driven browser control: observe, decide, act, repeat.

Selectors are the fast path for bulk collection — cheap, deterministic, and gated as
NFR-SC-2 requires. This is what runs when selectors cannot cope: a layout change, a
CAPTCHA or checkpoint, a login flow, or an operator asking the agent to go look at
something.

    screenshot + DOM digest → model → {action, x, y, text, reason} → act → repeat

Three things here are easy to get wrong and expensive to debug:

**Coordinates come back normalised (0-1000), not in pixels.** Scaling happens in one
place, `_to_viewport`, because getting it wrong produces clicks that land tens of
pixels off — a failure that looks like the model being bad at seeing.

**A DOM digest ships alongside the image.** It costs few tokens, and Arabic and
Kurdish script at screenshot resolution is genuinely hard to read; the digest carries
the text reliably while the image carries the layout.

**Budgets are enforced in code.** FR-AG-7 lists cost and rate limits among the
guardrails the agent cannot override, so a loop that fails to converge stops on a step
ceiling rather than spending until someone notices.
"""
from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.browsers.cursor import HumanCursor
from src.classifiers.llm_client import LLMClient
from src.core.settings import get_settings

log = structlog.get_logger()

PROMPT_VERSION = "vision-v1"

# Gemini returns spatial coordinates on a fixed 0-1000 grid regardless of image size.
NORMALISED_SCALE = 1000


class Action(str, Enum):
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    NAVIGATE = "navigate"
    EXTRACT = "extract"
    DONE = "done"
    BLOCKED = "blocked"  # cannot proceed — hand back to a human


class VisionStep(BaseModel):
    action: Action
    x: int | None = Field(default=None, description="Normalised 0-1000 horizontal position")
    y: int | None = Field(default=None, description="Normalised 0-1000 vertical position")
    text: str | None = Field(default=None, description="Text to type, or URL to navigate to")
    scroll_amount: int | None = Field(default=None, description="Pixels to scroll, may be negative")
    extracted: str | None = Field(default=None, description="Content read from the page")
    reason: str = Field(description="Why this action, in one sentence")


SYSTEM = """You operate a web browser for a human rights organisation that monitors \
hate speech against minority communities in Iraq. You are given a screenshot and a \
text digest of the page, and you choose ONE action at a time.

You are here because automated selectors failed — the layout changed, a checkpoint \
appeared, or a human asked you to look at something.

Coordinates are on a 0-1000 grid over the image, where (0,0) is top-left.

Rules:
- One action per step. You will see the result before choosing the next.
- Prefer scrolling and reading over clicking. Clicks change state; reading does not.
- Never click anything that submits a report, deletes content, or posts publicly.
- If you meet a CAPTCHA or an identity checkpoint, answer `blocked` and explain what \
is on screen. A human will take over. Do not attempt to solve it.
- If you cannot make progress in a few steps, answer `blocked` rather than guessing.
- When you have what was asked for, answer `done` and put the content in `extracted`.

Always give a short concrete reason. It is written to an audit log a human will read."""


class VisionBlocked(RuntimeError):
    """The agent needs a human — CAPTCHA, checkpoint, or a dead end."""


class VisionAgent:
    """Drives a page visually when selectors cannot."""

    def __init__(self, session: AsyncSession, page, llm: LLMClient | None = None):
        self.session = session
        self.page = page
        self.settings = get_settings()
        self.llm = llm or LLMClient(session)
        self.cursor = HumanCursor(page)
        self.steps: list[dict] = []

    async def run(self, goal: str, *, max_steps: int | None = None) -> dict:
        """Pursue `goal` until done, blocked, or out of steps."""
        max_steps = max_steps or self.settings.vision_max_steps_per_task
        self._check_domain(self.page.url)

        for step_number in range(max_steps):
            screenshot = await self.page.screenshot()
            digest = await self._digest()

            decision = await self.llm.generate(
                model=self.settings.vision_model,
                prompt=(
                    f"GOAL: {goal}\n\n"
                    f"CURRENT URL: {self.page.url}\n\n"
                    f"PAGE TEXT DIGEST:\n{digest}\n\n"
                    f"STEPS SO FAR: {len(self.steps)}/{max_steps}"
                ),
                schema=VisionStep,
                purpose="vision",
                prompt_version=PROMPT_VERSION,
                system_instruction=SYSTEM,
                images=[screenshot],
            )

            # Every step is recorded with its reasoning — NFR-AU-2 requires agent
            # decisions to retain the trace that produced them.
            record = {
                "step": step_number,
                "action": decision.action.value,
                "reason": decision.reason,
                "url": self.page.url,
            }
            self.steps.append(record)
            log.info("Vision step", **record)

            if decision.action is Action.DONE:
                return {"status": "done", "extracted": decision.extracted, "steps": self.steps}
            if decision.action is Action.BLOCKED:
                raise VisionBlocked(decision.reason)

            await self._act(decision)

        raise VisionBlocked(f"no resolution within {max_steps} steps")

    # ------------------------------------------------------------------ acting

    async def _act(self, decision: VisionStep) -> None:
        if decision.action is Action.CLICK:
            x, y = await self._to_viewport(decision.x, decision.y)
            await self.cursor.click(x, y)
        elif decision.action is Action.TYPE:
            await self.page.keyboard.type(decision.text or "", delay=80)
        elif decision.action is Action.SCROLL:
            await self.page.mouse.wheel(0, decision.scroll_amount or 600)
        elif decision.action is Action.NAVIGATE:
            self._check_domain(decision.text or "")
            await self.page.goto(decision.text, wait_until="domcontentloaded")
        # EXTRACT needs no page interaction — the content is already in the response.

    async def _to_viewport(self, x: int | None, y: int | None) -> tuple[float, float]:
        """Convert the model's 0-1000 grid to real pixels.

        The single place this conversion happens. Doing it inconsistently is the
        classic cause of clicks landing near, but not on, the target.
        """
        if x is None or y is None:
            raise VisionBlocked("click requested without coordinates")
        viewport = self.page.viewport_size or {"width": 1280, "height": 800}
        return (
            x / NORMALISED_SCALE * viewport["width"],
            y / NORMALISED_SCALE * viewport["height"],
        )

    async def _digest(self, limit: int = 4000) -> str:
        """Visible text, to carry script the screenshot renders poorly."""
        try:
            text = await self.page.inner_text("body")
        except Exception:  # page mid-navigation
            return "(page text unavailable)"
        return text[:limit]

    def _check_domain(self, url: str) -> None:
        """Domain allowlist — a guardrail in code, not in the prompt (FR-AG-7)."""
        allowed = self.settings.vision_allowed_domains
        if not allowed:
            return
        host = (urlparse(url).hostname or "").lower()
        if not any(host == d or host.endswith(f".{d}") for d in allowed):
            raise VisionBlocked(f"{host!r} is not in the vision domain allowlist")

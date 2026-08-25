"""Human-like pointer movement.

Playwright's `element.click()` teleports the cursor: no intermediate positions, no
acceleration, exact pixel centre, zero variance. Behavioural fingerprinting notices
that, and FR-CO-4 treats human-like pacing as an operational survival requirement
rather than a nicety.

Both collection paths click through here — the selector adapters and the vision
agent — so every interaction leaves a plausible trajectory.

What real pointer movement looks like, and what this reproduces:

* a curved path, not a straight line — hand and arm rotation bends it
* speed that eases in and out rather than being constant
* overshoot on longer moves, then a small correction back
* sub-pixel jitter throughout
* click points scattered within a target, not always dead centre
"""
from __future__ import annotations

import asyncio
import math
import random

import structlog

log = structlog.get_logger()

# Longer moves get more intermediate points; very short ones need barely any.
_MIN_STEPS = 8
_MAX_STEPS = 60
_OVERSHOOT_DISTANCE = 220  # px, below which people rarely overshoot


def _ease(t: float) -> float:
    """Ease-in-out. Constant velocity is the giveaway a straight line already gives."""
    return 3 * t**2 - 2 * t**3


def bezier_points(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    steps: int | None = None,
    curvature: float = 0.25,
    rng: random.Random | None = None,
) -> list[tuple[float, float]]:
    """Cubic Bézier path from start to end, curved perpendicular to the direct line.

    Pure function so the shape can be tested without a browser.
    """
    rng = rng or random
    x0, y0 = start
    x1, y1 = end
    distance = math.hypot(x1 - x0, y1 - y0)

    if steps is None:
        steps = max(_MIN_STEPS, min(_MAX_STEPS, int(distance / 12)))
    if distance == 0:
        return [(x1, y1)]

    # Control points pushed off the direct line, sign chosen at random so paths
    # do not all bow the same way.
    nx, ny = -(y1 - y0) / distance, (x1 - x0) / distance
    bow = distance * curvature * rng.uniform(0.4, 1.0) * rng.choice((-1, 1))

    cx1 = x0 + (x1 - x0) / 3 + nx * bow
    cy1 = y0 + (y1 - y0) / 3 + ny * bow
    cx2 = x0 + 2 * (x1 - x0) / 3 + nx * bow * rng.uniform(0.5, 1.0)
    cy2 = y0 + 2 * (y1 - y0) / 3 + ny * bow * rng.uniform(0.5, 1.0)

    points: list[tuple[float, float]] = []
    for step in range(steps + 1):
        t = _ease(step / steps)
        u = 1 - t
        x = u**3 * x0 + 3 * u**2 * t * cx1 + 3 * u * t**2 * cx2 + t**3 * x1
        y = u**3 * y0 + 3 * u**2 * t * cy1 + 3 * u * t**2 * cy2 + t**3 * y1
        # Sub-pixel jitter; hands are not smooth.
        points.append((x + rng.gauss(0, 0.4), y + rng.gauss(0, 0.4)))

    points[-1] = (x1, y1)  # always land exactly on target
    return points


def click_point_within(
    box: dict, *, rng: random.Random | None = None
) -> tuple[float, float]:
    """Pick a plausible click point inside a bounding box.

    Biased toward the middle but never exactly centre — repeated pixel-perfect
    centre clicks are themselves a signal.
    """
    rng = rng or random
    return (
        box["x"] + box["width"] * rng.gauss(0.5, 0.15),
        box["y"] + box["height"] * rng.gauss(0.5, 0.15),
    )


class HumanCursor:
    """Moves a Playwright mouse the way a hand would."""

    def __init__(self, page, rng: random.Random | None = None):
        self.page = page
        self.rng = rng or random
        self._x = 0.0
        self._y = 0.0

    async def move_to(self, x: float, y: float) -> None:
        distance = math.hypot(x - self._x, y - self._y)
        target = (x, y)

        # People overshoot longer movements and correct back.
        if distance > _OVERSHOOT_DISTANCE and self.rng.random() < 0.7:
            overshoot = (
                x + (x - self._x) / distance * self.rng.uniform(4, 18),
                y + (y - self._y) / distance * self.rng.uniform(4, 18),
            )
            await self._trace(bezier_points((self._x, self._y), overshoot, rng=self.rng))
            await asyncio.sleep(self.rng.uniform(0.02, 0.06))
            await self._trace(bezier_points(overshoot, target, steps=6, rng=self.rng))
        else:
            await self._trace(bezier_points((self._x, self._y), target, rng=self.rng))

        self._x, self._y = target

    async def _trace(self, points: list[tuple[float, float]]) -> None:
        for x, y in points:
            await self.page.mouse.move(x, y)
            await asyncio.sleep(self.rng.uniform(0.004, 0.016))

    async def click(self, x: float, y: float) -> None:
        await self.move_to(x, y)
        # Pause before pressing: people do not click the instant they arrive.
        await asyncio.sleep(self.rng.uniform(0.04, 0.18))
        await self.page.mouse.down()
        await asyncio.sleep(self.rng.uniform(0.05, 0.13))  # dwell time
        await self.page.mouse.up()

    async def click_selector(self, selector: str) -> bool:
        """Click an element through the cursor rather than teleporting to it."""
        element = await self.page.query_selector(selector)
        if element is None:
            return False
        box = await element.bounding_box()
        if box is None:  # off-screen or not rendered
            return False
        x, y = click_point_within(box, rng=self.rng)
        await self.click(x, y)
        return True

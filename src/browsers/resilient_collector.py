"""Selector-first collection with a vision fallback.

The hybrid contract, in one place. Selectors run first because they are fast, cheap
and deterministic (NFR-SC-2). When they break — and on these platforms they will —
this decides what happens next based on *why* they broke, because the three causes
need completely different responses:

* **layout changed** — the vision agent re-derives the page so the run still
  completes, and proposes replacement selectors for a human to accept. Collection
  does not stall waiting for a developer.
* **CAPTCHA or checkpoint** — a human is asked. The agent must not attempt these:
  failed attempts are themselves a strong bot signal, and solving them is not the
  agent's job.
* **session lost** — the account needs re-authenticating and is quarantined until
  it is (FR-CO-3), rather than repeatedly hitting a login wall.

The proposal, not repair, split matters: a selector the model invented becomes part
of what the system watches with, so it goes to a human the same way a lexicon term
does (FR-LE-1's reasoning applied to collection).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.browsers.vision_agent import VisionAgent, VisionBlocked
from src.notifications.dispatcher import NotificationDispatcher
from src.platforms.base_adapter import PlatformAdapter, SelectorsBroken

log = structlog.get_logger()

# Substrings in a SelectorsBroken reason that mean "a human is required", not
# "the layout moved".
_NEEDS_HUMAN = ("captcha", "checkpoint", "login wall", "session lost")


@dataclass
class CollectionOutcome:
    items: list[dict] = field(default_factory=list)
    used_vision: bool = False
    needs_human: bool = False
    reason: str | None = None
    proposed_selectors: dict[str, str] = field(default_factory=dict)


class ResilientCollector:
    """Runs an adapter, falling back to vision when its selectors fail."""

    def __init__(self, session: AsyncSession, page, adapter: PlatformAdapter):
        self.session = session
        self.page = page
        self.adapter = adapter
        self.dispatcher = NotificationDispatcher(session)

    async def fetch_comments(self, post_url: str, max_comments: int = 100) -> CollectionOutcome:
        try:
            items = await self.adapter.fetch_comments(self.page, post_url, max_comments)
            return CollectionOutcome(items=items)
        except SelectorsBroken as broken:
            return await self._recover(broken, post_url, max_comments)

    async def _recover(
        self, broken: SelectorsBroken, url: str, max_comments: int
    ) -> CollectionOutcome:
        reason = ", ".join(broken.missing)

        if any(marker in reason.lower() for marker in _NEEDS_HUMAN):
            return await self._escalate(broken, reason)

        log.warning("Selectors broke, falling back to vision", platform=broken.platform, missing=broken.missing)

        agent = VisionAgent(self.session, self.page)
        goal = (
            f"Read the visible comments on this {broken.platform} post and return them "
            f"as a plain list, one per line. The usual page structure has changed."
        )
        try:
            result = await agent.run(goal)
        except VisionBlocked as blocked:
            return await self._escalate(broken, str(blocked))

        items = [
            {"platform_comment_id": f"vision-{i}", "text": line.strip(), "author_name": None}
            for i, line in enumerate((result.get("extracted") or "").splitlines())
            if line.strip()
        ][:max_comments]

        # Ask separately for replacement selectors. Kept as a distinct step so a
        # failure to propose does not lose the comments already recovered.
        proposed = await self._propose_selectors(agent, broken)

        await self.dispatcher.send(
            type_="SelectorRepair",
            context={
                "platform": broken.platform,
                "missing": broken.missing,
                "url": url,
                "recovered_items": len(items),
                "proposed_selectors": proposed,
            },
            question=(
                f"{broken.platform} selectors {broken.missing} stopped matching. Vision "
                f"recovered {len(items)} comments and proposes replacements. Accept?"
            ),
            urgency="High",
            suggested_actions=["Accept proposed selectors", "Review manually", "Pause platform"],
        )

        return CollectionOutcome(
            items=items, used_vision=True, reason=reason, proposed_selectors=proposed
        )

    async def _propose_selectors(
        self, agent: VisionAgent, broken: SelectorsBroken
    ) -> dict[str, str]:
        """Ask the model for CSS that would match again.

        Proposals only — never applied automatically. A selector the model invented
        silently becomes part of what the system watches with, and a wrong one means
        collecting the wrong elements without any error.
        """
        try:
            result = await agent.run(
                f"Inspect this page and propose CSS selectors that would match: "
                f"{', '.join(broken.missing)}. Return them as `key: selector` lines.",
                max_steps=3,
            )
        except VisionBlocked as exc:
            log.info("No selector proposal available", error=str(exc))
            return {}

        proposed: dict[str, str] = {}
        for line in (result.get("extracted") or "").splitlines():
            if ":" in line:
                key, _, selector = line.partition(":")
                key, selector = key.strip(), selector.strip()
                if key in broken.missing and selector:
                    proposed[key] = selector
        return proposed

    async def _escalate(self, broken: SelectorsBroken, reason: str) -> CollectionOutcome:
        """Hand to a human, and say what kind of help is needed."""
        log.warning("Human intervention required", platform=broken.platform, reason=reason)
        await self.dispatcher.send(
            type_="HumanInterventionRequired",
            context={"platform": broken.platform, "url": broken.url, "reason": reason},
            question=(
                f"{broken.platform} needs a human: {reason}. "
                "Open the live browser view to take control."
            ),
            urgency="Critical",
            suggested_actions=["Take control of the browser", "Rotate account", "Pause platform"],
        )
        return CollectionOutcome(needs_human=True, reason=reason)

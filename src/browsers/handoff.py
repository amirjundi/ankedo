"""Hand a blocked browser session to a human.

CAPTCHAs and identity checkpoints are routine on Facebook and Instagram, and the
agent must not attempt them: a failed attempt is itself a strong bot signal, and
solving them is not what it is for.

Without a handoff path, every checkpoint is a dead end that needs someone to notice
the account went quiet and shell into the machine. This makes it an explicit request
with a deadline:

1. capture what the agent is looking at
2. alert the admin through whatever channels are configured, with the screenshot
3. hold the session open and poll for the block to clear
4. on timeout, quarantine the account rather than hammering the checkpoint

The dedicated PC matters here — the browser can be headed and the operator can act
directly on the machine, so the artifacts written here are the fallback for when
nobody is sitting at it, not the only route.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.settings import get_settings
from src.models.agent_worker_account import AccountStage, AccountState, AgentWorkerAccount
from src.notifications.dispatcher import NotificationDispatcher

log = structlog.get_logger()


class HandoffTimeout(RuntimeError):
    """Nobody resolved the block within the window."""


class HumanHandoff:
    """Requests human help on a blocked session and waits for it."""

    def __init__(self, session: AsyncSession, page, worker: AgentWorkerAccount):
        self.session = session
        self.page = page
        self.worker = worker
        self.settings = get_settings()
        self.dispatcher = NotificationDispatcher(session)

    async def request(self, reason: str, *, kind: str = "captcha") -> bool:
        """Ask for help and wait. True if cleared, False if it timed out.

        The account is quarantined on timeout rather than retried — an unattended
        checkpoint that keeps being hit is how an account gets permanently banned
        rather than temporarily challenged.
        """
        shot = await self._capture()

        await self.dispatcher.send(
            type_="HumanInterventionRequired",
            context={
                "kind": kind,
                "platform": self.worker.platform,
                "account": self.worker.username,
                "url": self.page.url,
                "screenshot": str(shot) if shot else None,
                "headed": not self.settings.browser_headless,
            },
            question=(
                f"{self.worker.platform} hit a {kind} on account "
                f"{self.worker.username}. The agent will not attempt it. "
                f"Resolve it in the browser within "
                f"{self.settings.handoff_timeout_minutes} minutes, or the account "
                f"is quarantined."
            ),
            urgency="Critical",
            suggested_actions=[
                "Solve it in the live browser",
                "Rotate to another account",
                "Pause this platform",
            ],
        )

        cleared = await self._wait_for_clear(kind)
        if cleared:
            log.info("Block cleared by human", account=self.worker.username, kind=kind)
            return True

        await self._quarantine(reason)
        return False

    async def _capture(self) -> Path | None:
        """Screenshot the blocked page so the admin can see it from a phone."""
        try:
            directory = Path(self.settings.data_dir).parent / "screenshots" / "handoff"
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            path = directory / f"{self.worker.platform}-{self.worker.username}-{stamp}.png"
            await self.page.screenshot(path=str(path))
            return path
        except Exception as exc:
            # A failed screenshot must not swallow the alert — the alert is the point.
            log.warning("Could not capture handoff screenshot", error=str(exc))
            return None

    async def _wait_for_clear(self, kind: str) -> bool:
        """Poll until the blocking element disappears or the window closes."""
        deadline = self.settings.handoff_timeout_minutes * 60
        interval = self.settings.handoff_poll_seconds
        waited = 0

        while waited < deadline:
            await asyncio.sleep(interval)
            waited += interval
            try:
                if not await self._still_blocked(kind):
                    return True
            except Exception as exc:
                # A closed page means the human took over destructively; treat it as
                # unresolved rather than assuming success.
                log.warning("Handoff polling failed", error=str(exc))
                return False

        return False

    async def _still_blocked(self, kind: str) -> bool:
        markers = ["captcha", "checkpoint"] if kind == "captcha" else ["login", "checkpoint"]
        url = (self.page.url or "").lower()
        if any(marker in url for marker in markers):
            return True
        content = (await self.page.content()).lower()
        return any(marker in content for marker in markers)

    async def _quarantine(self, reason: str) -> None:
        self.worker.state = AccountState.BLOCKED
        self.worker.stage = AccountStage.QUARANTINE
        await self.session.commit()

        log.error(
            "Account quarantined after unanswered handoff",
            account=self.worker.username,
            platform=self.worker.platform,
        )
        await self.dispatcher.send(
            type_="AccountQuarantined",
            context={
                "account": self.worker.username,
                "platform": self.worker.platform,
                "reason": reason,
            },
            question=(
                f"{self.worker.username} was quarantined after "
                f"{self.settings.handoff_timeout_minutes} minutes with no response. "
                "It will not be used until reactivated."
            ),
            urgency="High",
            suggested_actions=["Reactivate account", "Provision a replacement", "Acknowledge"],
        )

"""HTTP client for the Ettok agent API.

Implements `docs/AGENT_CONTRACT.md`. Two rules from the contract drive the error
handling here:

* 401 (unknown/revoked/missing key) and 403 (valid key, wrong scope) must **stop and
  alert, never retry** — retrying a revoked key is how an agent hammers a server it
  has already been ejected from.
* Everything else is transient and retried with backoff. This agent runs on
  residential WiFi, so connection drops are the normal case, not an exception.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

from src.core.settings import get_settings

log = structlog.get_logger()


class EttokError(RuntimeError):
    """Base for platform communication failures."""


class AgentKeyRejected(EttokError):
    """401/403 — the key is unknown, revoked, or lacks the scope.

    Never retried. The operator has to issue a new key in the Django admin.
    """


class EttokUnavailable(EttokError):
    """Network failure or 5xx that survived the retries."""


def _is_loopback(url: str) -> bool:
    """Allow http:// only against localhost, for development against a local server."""
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


class EttokClient:
    """Async client for `/api/hermes/`.

    Usable as an async context manager, or with an injected transport for tests.
    """

    def __init__(
        self,
        base_url: str | None = None,
        agent_key: str | None = None,
        agent_id: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.ettok_base_url).rstrip("/") + "/"
        self.agent_key = agent_key or settings.ettok_agent_key
        self.agent_id = agent_id or settings.ettok_agent_id
        self.max_retries = settings.ettok_max_retries

        if not self.agent_key:
            raise EttokError(
                "no agent key configured — set ETTOK_AGENT_KEY (issued in the Django "
                "admin under 'Agent keys' with the hate_speech_scan scope)"
            )

        # HTTPS is not optional. This carries evidence about people who are already
        # targets of violence, plus a bearer token that grants submission rights, over
        # residential WiFi in Iraq. A plaintext URL exposes both to anyone on the path,
        # and the contract says HTTPS only — so a downgrade is refused rather than
        # warned about, since a warning in a log nobody reads is not a control.
        if not self.base_url.startswith("https://"):
            if not _is_loopback(self.base_url):
                raise EttokError(
                    f"refusing to connect over plaintext: {self.base_url!r}. "
                    "Use https:// — the contract is HTTPS only, and this carries "
                    "evidence about vulnerable people."
                )

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=settings.ettok_timeout_seconds,
            transport=transport,
            headers={
                "Authorization": f"Bearer {self.agent_key}",
                "X-Agent-Id": self.agent_id,
                "Accept": "application/json",
            },
        )

    async def __aenter__(self) -> EttokClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ core

    async def _request(
        self, method: str, path: str, *, request_id: str | None = None, **kwargs: Any
    ) -> dict:
        """Send, retrying transient failures.

        `request_id` makes a write idempotent. The retry loop cannot tell "the server
        never saw it" from "the server processed it and the response was lost" — on a
        residential connection the second happens — so without a stable id a retry
        creates duplicate reports about the same person.
        """
        last_error: Exception | None = None
        if request_id:
            kwargs.setdefault("headers", {})
            kwargs["headers"]["Idempotency-Key"] = request_id

        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(method, path, **kwargs)
            except httpx.HTTPError as exc:
                last_error = exc
                log.warning(
                    "Platform request failed", path=path, attempt=attempt + 1, error=str(exc)
                )
            else:
                if response.status_code in (401, 403):
                    # Contract: stop and alert. Retrying cannot help and looks hostile.
                    raise AgentKeyRejected(
                        f"{response.status_code} from {path} — "
                        + (
                            "key unknown, revoked or missing"
                            if response.status_code == 401
                            else "key lacks the hate_speech_scan scope"
                        )
                    )
                if response.status_code >= 500:
                    last_error = EttokUnavailable(f"{response.status_code} from {path}")
                    log.warning(
                        "Platform server error",
                        path=path,
                        status=response.status_code,
                        attempt=attempt + 1,
                    )
                elif response.status_code >= 400:
                    # 4xx other than auth is our bug — a malformed payload will not
                    # fix itself on retry.
                    raise EttokError(f"{response.status_code} from {path}: {response.text[:300]}")
                else:
                    return response.json() if response.content else {}

            if attempt < self.max_retries:
                await asyncio.sleep(2**attempt)  # 1s, 2s, 4s

        raise EttokUnavailable(f"{path} failed after {self.max_retries + 1} attempts: {last_error}")

    # ------------------------------------------------------------- endpoints

    async def heartbeat(self, status: str = "idle") -> dict:
        """Announce liveness; receive config and possibly a scan trigger.

        `scan_requested` is one-shot — the platform clears the flag when handing it
        over, so a caller that drops the response has lost that scan.
        """
        response = await self._request(
            "POST", "heartbeat/", json={"agent_id": self.agent_id, "status": status}
        )
        self._maybe_rotate_key(response)
        return response

    def _maybe_rotate_key(self, response: dict) -> None:
        """Adopt a replacement key the platform offers on heartbeat.

        Rotation only happens in practice if it costs nothing. The platform issues the
        new key while the old one still works, the agent picks it up here, and the old
        one is revoked after the overlap window — so no scan is missed and nobody has
        to schedule downtime.

        The new key is written to .env rather than held in memory, or the next restart
        would fall back to a key that is about to be revoked.
        """
        rotated = response.get("rotate_key")
        if not rotated or rotated == self.agent_key:
            return

        self.agent_key = rotated
        self._client.headers["Authorization"] = f"Bearer {rotated}"

        try:
            _persist_key(rotated)
            log.warning(
                "Agent key rotated by the platform — .env updated",
                agent_id=self.agent_id,
            )
        except OSError as exc:
            # In memory the new key still works for this run; a restart would revert
            # to the old one, so this needs a human before the overlap window closes.
            log.error(
                "Key rotated but .env could not be written. This run continues on "
                "the new key, but the file still holds the old one — so it will work "
                "until the agent is restarted, then fail with 401 once the old key is "
                "revoked. Write the new key to .env before restarting.",
                error=str(exc),
            )

    async def get_tasks(self) -> dict:
        """What to scan this run."""
        return await self._request("GET", "tasks/")

    async def get_lexicon(self, languages: list[str] | None = None) -> dict:
        """The full active term list to prefilter with."""
        params = [("language", lang) for lang in languages] if languages else None
        return await self._request("GET", "lexicon/", params=params)

    async def get_tropes(self, target_groups: list[str] | None = None) -> dict:
        """The trope dictionary — coded speech that is hateful only in context.

        `GET lexicon/` also returns these under a `tropes` key, so fetch both together
        when syncing everything; this exists for pulling tropes on their own cadence.
        """
        params = [("target_group", g) for g in target_groups] if target_groups else None
        return await self._request("GET", "tropes/", params=params)

    async def get_accounts(self) -> dict:
        """Monitoring accounts to browse as."""
        return await self._request("GET", "accounts/")

    async def post_flagged_items(self, items: list[dict], *, request_id: str | None = None) -> dict:
        """Submit suspicious content.

        Carries the agent's verdict (§7), not merely a candidate. Confirmation on the
        platform is asynchronous with no callback — results surface as
        HateSpeechReport rows for human review.

        `request_id` is sent as an Idempotency-Key so a retry after a lost response
        cannot create a second report about the same person.
        """
        return await self._request(
            "POST", "flagged-items/", json={"items": items}, request_id=request_id
        )

    async def post_lexicon_gaps(self, gaps: list[dict]) -> dict:
        """Propose terms the agent saw but the dictionary does not have (§3).

        The mechanism by which the agent contributes without authority to rewrite its
        own rules: it proposes, a curator accepts. Blocked upstream until
        LexiconGap.report becomes nullable — a proposal has no report to hang off.
        """
        return await self._request("POST", "lexicon-gaps/", json={"gaps": gaps})

    async def post_scan_log(self, payload: dict) -> dict:
        """Submit run statistics."""
        return await self._request("POST", "scan-log/", json=payload)

    async def post_cookies(self, payload: dict) -> dict:
        """Persist refreshed session cookies."""
        return await self._request("POST", "cookies/", json=payload)


def _persist_key(new_key: str) -> None:
    """Replace ETTOK_AGENT_KEY in .env, leaving every other line untouched.

    Written to disk rather than kept in memory: the old key is revoked once the
    overlap window closes, so an agent that only held the new one in memory would
    come back after a restart using a key that no longer works.
    """
    from pathlib import Path

    env = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env.exists():
        return

    lines = env.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.strip().startswith("ETTOK_AGENT_KEY="):
            lines[index] = f"ETTOK_AGENT_KEY={new_key}"
            break
    else:
        lines.append(f"ETTOK_AGENT_KEY={new_key}")

    env.write_text("\n".join(lines) + "\n", encoding="utf-8")

"""Enrol an agent using a short-lived pairing code.

The problem this solves is the handover. An operator has to get a credential from an
admin onto a machine, and in practice that happens over WhatsApp, Telegram, or a phone
call. Sending the long-lived agent key that way puts a credential that never expires
into a chat log on two phones, a backup, and possibly a cloud sync — where it stays
after the conversation is forgotten.

So the admin issues a **pairing code** instead: short, single-use, and expiring in
minutes. The operator types it into `ankedo setup`; the agent exchanges it once for the
real key, which is written to `.env` and never transmitted by a human at all.

What that buys:

* the code is useless after one use, so a chat log holds nothing of value
* it expires, so a code sent and forgotten does not stay live
* it is short enough to read aloud without transcription errors
* the admin can see whether it was redeemed, and by which machine

The long-lived key still exists — it just never travels through a human channel.
"""
from __future__ import annotations

import re

import httpx
import structlog

log = structlog.get_logger()

# Grouped for reading aloud over a bad phone line. Ambiguous characters (0/O, 1/I)
# are expected to be excluded by the issuer for the same reason.
CODE_PATTERN = re.compile(r"^[A-Z0-9]{3,6}(-[A-Z0-9]{3,6}){1,3}$")


class EnrollmentError(RuntimeError):
    """The code was rejected, expired, already used, or unreachable."""


def normalise_code(raw: str) -> str:
    """Accept what a human actually types: spaces, lowercase, missing hyphens."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw or "").upper()
    if not cleaned:
        raise EnrollmentError("no code entered")

    # Already well-formed — keep the operator's grouping rather than re-deriving it,
    # since the issuer chose it and may not group in fours.
    tidied = raw.strip().upper()
    if CODE_PATTERN.match(tidied):
        return tidied

    # Typed without separators, or with spaces. Re-group into fours.
    return "-".join(cleaned[i : i + 4] for i in range(0, len(cleaned), 4))


def redeem(base_url: str, code: str, agent_id: str, *, timeout: float = 20.0) -> dict:
    """Exchange a pairing code for a long-lived agent key.

    Returns `{agent_key, agent_id, ...}`. Deliberately synchronous — this runs inside
    the setup wizard, where there is no event loop and a human is waiting.

    Unauthenticated by design: the code *is* the credential, which is why it has to be
    single-use and short-lived.
    """
    url = base_url.rstrip("/") + "/enroll/"
    if not url.startswith("https://") and not _is_local(url):
        raise EnrollmentError(
            "refusing to send a pairing code over plaintext — use https://"
        )

    try:
        response = httpx.post(
            url,
            json={"code": normalise_code(code), "agent_id": agent_id},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise EnrollmentError(f"could not reach {url} ({type(exc).__name__})") from exc

    if response.status_code == 404:
        raise EnrollmentError(
            "this platform does not support pairing codes — ask the admin for a key "
            "directly and paste it instead"
        )
    if response.status_code in (400, 401, 403, 410):
        # The distinction matters: a typo is retried, an expired code needs a new one.
        detail = _detail(response)
        raise EnrollmentError(detail or "code rejected — expired, already used, or mistyped")
    if response.status_code >= 400:
        raise EnrollmentError(f"platform returned HTTP {response.status_code}")

    payload = response.json()
    key = payload.get("agent_key")
    if not key:
        raise EnrollmentError("platform accepted the code but returned no agent key")

    log.info("Agent enrolled", agent_id=payload.get("agent_id", agent_id))
    return payload


def _detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return body.get("detail") or body.get("error") or ""


def _is_local(url: str) -> bool:
    from urllib.parse import urlparse

    return (urlparse(url).hostname or "").lower() in ("localhost", "127.0.0.1", "::1")

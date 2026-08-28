"""Reaching the dashboard through a tunnel, with the token entered once.

Two operator requirements, and one hazard that sits between them.

The token is now stored in localStorage rather than sessionStorage, so it is typed
once instead of on every tab. The original reasoning — keep it off disk on a machine
holding evidence — was wrong about where the risk is: retyping a 32-character token
constantly is the kind of friction people solve by choosing a short token, writing it
on a note, or turning auth off. And it is already on that disk, in .env, in plaintext.

The hazard is the tunnel. Cloudflare's daemon runs on the agent's own machine, so a
request from the public internet arrives from 127.0.0.1 and looks exactly like someone
typing at the keyboard. That is the case that must not be trusted.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient as _TestClient

from src.core.settings import get_settings

ROOT = Path(__file__).resolve().parents[3]
API_JS = ROOT / "frontend" / "src" / "api.js"
TOKEN = "tunnel-token"
PROTECTED = "/api/admin/health"

TUNNEL = {"cf-connecting-ip": "203.0.113.9", "host": "ankedo.example.com"}


def TestClient(app):  # noqa: N802
    return _TestClient(app, client=("127.0.0.1", 51234))


@pytest_asyncio.fixture
async def app_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'r.db'}")
    monkeypatch.setenv("RUN_AGENT_WITH_API", "false")

    import src.models.base as base

    def build(**env):
        for key in ("ADMIN_API_TOKEN", "PUBLIC_DASHBOARD_URL"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        base._engine = None
        base._async_session_factory = None

        import asyncio
        import importlib

        from src.core.database import init_db

        asyncio.get_event_loop().run_until_complete(init_db())

        import src.api.main as main

        importlib.reload(main)  # CORS origins are computed at import time
        return main

    yield build

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


# ── the tunnel ───────────────────────────────────────────────────────────────


def test_a_tunnelled_request_needs_the_token(app_factory):
    """The whole hazard: the socket says 127.0.0.1, the request came from the
    internet."""
    main = app_factory(ADMIN_API_TOKEN=TOKEN)
    with TestClient(main.app) as c:
        assert c.get(PROTECTED, headers=TUNNEL).status_code == 401


def test_a_tunnelled_request_with_the_token_is_served(app_factory):
    main = app_factory(ADMIN_API_TOKEN=TOKEN)
    with TestClient(main.app) as c:
        response = c.get(
            PROTECTED, headers={**TUNNEL, "Authorization": f"Bearer {TOKEN}"}
        )

    assert response.status_code == 200


def test_a_tunnel_with_no_token_configured_refuses_and_says_why(app_factory):
    """503, not a silent open dashboard — and it names the command that fixes it."""
    main = app_factory()
    with TestClient(main.app) as c:
        response = c.get(PROTECTED, headers=TUNNEL)

    assert response.status_code == 503
    assert "ankedo token" in response.json()["detail"]


# ── the public URL ───────────────────────────────────────────────────────────


def test_the_public_domain_is_added_to_cors(app_factory):
    main = app_factory(ADMIN_API_TOKEN=TOKEN,
                       PUBLIC_DASHBOARD_URL="https://ankedo.example.com")

    assert "https://ankedo.example.com" in main._origins


def test_a_pasted_url_with_a_path_is_normalised(app_factory):
    """A path is not a valid Origin. Left as pasted, the browser compares it against
    a value that can never match and reports a CORS error naming an origin that looks
    correct."""
    main = app_factory(ADMIN_API_TOKEN=TOKEN,
                       PUBLIC_DASHBOARD_URL="https://ankedo.example.com/dashboard")

    assert "https://ankedo.example.com" in main._origins
    assert "https://ankedo.example.com/dashboard" not in main._origins


def test_nonsense_is_ignored_rather_than_widening_cors(app_factory):
    main = app_factory(ADMIN_API_TOKEN=TOKEN, PUBLIC_DASHBOARD_URL="not a url")

    assert all(o.startswith("http") for o in main._origins)
    assert "*" not in main._origins


def test_localhost_still_works_when_a_domain_is_set(app_factory):
    """Configuring a tunnel must not lock the operator out of their own machine."""
    main = app_factory(PUBLIC_DASHBOARD_URL="https://ankedo.example.com")
    with TestClient(main.app) as c:
        assert c.get(PROTECTED).status_code == 200


# ── entered once ─────────────────────────────────────────────────────────────


def _code_only(text: str) -> str:
    """Drop // comments. A comment explaining the old behaviour mentions
    sessionStorage, and would otherwise read as the old behaviour."""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("//")
    )


def test_the_token_survives_closing_the_tab():
    code = _code_only(API_JS.read_text(encoding="utf-8"))

    assert "localStorage" in code
    assert "sessionStorage" not in code, "the token would be retyped on every tab"


def test_a_rejected_token_is_discarded():
    """A stored token the agent refuses would otherwise be resent forever, and the
    prompt to replace it would never be reachable."""
    source = API_JS.read_text(encoding="utf-8")

    match = re.search(r"401 \|\| res\.status === 403\) \{(.{0,200})", source, re.DOTALL)
    assert match, "could not find the auth-failure branch"
    assert "clearToken" in match.group(1)


def test_storage_access_failures_do_not_break_the_dashboard():
    """Private browsing and hardened configurations throw on access rather than
    returning null. An unreadable store is an absent one, not a crashed page."""
    source = API_JS.read_text(encoding="utf-8")

    assert source.count("try {") >= 3

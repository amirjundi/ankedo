"""No password on your own machine; a password the moment it leaves it.

Requiring a token from someone already sitting at the computer protects nobody — it
is a prompt between a person and software on their own laptop. Requiring one from the
network is the only thing standing between a stranger and a database of verdicts
naming people who are already targets of violence.

So the decision is made from where the request came from, not from configuration.

The subtle case is a tunnel. Cloudflare's daemon runs on the same machine, so a
request from the public internet arrives at the agent from 127.0.0.1. Trusting the
peer address alone would publish the dashboard to anyone with the URL while looking
exactly like someone typing on the laptop — which is the failure this file exists to
prevent, and the deployment the operator has said they intend.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient as _TestClient


def TestClient(app):  # noqa: N802 — a drop-in with a realistic peer address
    """Starlette reports the client host as "testclient", which is not an address and
    is not loopback, so every request looked remote. Real uvicorn hands the app
    127.0.0.1 for a local connection — pin that, or these tests exercise a code path
    that cannot occur in production."""
    return _TestClient(app, client=("127.0.0.1", 51234))

from src.core.settings import get_settings

TOKEN = "a-real-token"
PROTECTED = "/api/admin/health"


@pytest_asyncio.fixture
async def app_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    monkeypatch.setenv("RUN_AGENT_WITH_API", "false")

    import src.models.base as base

    def build(token: str | None):
        if token is None:
            monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
        else:
            monkeypatch.setenv("ADMIN_API_TOKEN", token)
        get_settings.cache_clear()
        base._engine = None
        base._async_session_factory = None

        import asyncio

        from src.core.database import init_db

        asyncio.get_event_loop().run_until_complete(init_db())
        from src.api.main import app

        return app

    yield build

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


# ── no token configured ──────────────────────────────────────────────────────


def test_a_local_request_is_served_without_a_token(app_factory):
    """The operator on their own machine. TestClient's default client is 127.0.0.1
    with no forwarding headers, which is exactly the local case."""
    with TestClient(app_factory(None)) as c:
        assert c.get(PROTECTED).status_code == 200


def test_a_request_through_a_tunnel_is_refused(app_factory):
    """cf-connecting-ip means Cloudflare forwarded this. The socket says 127.0.0.1
    and the request came from the internet."""
    with TestClient(app_factory(None)) as c:
        response = c.get(PROTECTED, headers={"cf-connecting-ip": "203.0.113.9"})

    assert response.status_code == 503
    assert "ankedo token" in response.json()["detail"]


@pytest.mark.parametrize(
    "header", ["x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip"]
)
def test_any_forwarding_header_means_not_local(app_factory, header):
    """Every reverse proxy sets one of these. Whichever it is, the peer address is
    the proxy and says nothing about who is really asking."""
    with TestClient(app_factory(None)) as c:
        assert c.get(PROTECTED, headers={header: "203.0.113.9"}).status_code == 503


# ── token configured ─────────────────────────────────────────────────────────


def test_a_configured_token_is_still_required_locally(app_factory):
    """Setting a token is an instruction, not a suggestion. An operator who
    configured one has decided this agent needs it — honouring that only for remote
    callers would silently weaken what they asked for."""
    with TestClient(app_factory(TOKEN)) as c:
        assert c.get(PROTECTED).status_code == 401


def test_the_right_token_works(app_factory):
    with TestClient(app_factory(TOKEN)) as c:
        response = c.get(PROTECTED, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 200


def test_a_wrong_token_is_rejected_even_locally(app_factory):
    with TestClient(app_factory(TOKEN)) as c:
        response = c.get(PROTECTED, headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 401


def test_a_tunnelled_request_with_the_right_token_works(app_factory):
    """The intended remote deployment: exposed, and authenticated."""
    with TestClient(app_factory(TOKEN)) as c:
        response = c.get(
            PROTECTED,
            headers={"Authorization": f"Bearer {TOKEN}", "cf-connecting-ip": "203.0.113.9"},
        )

    assert response.status_code == 200

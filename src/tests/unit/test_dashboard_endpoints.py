"""The endpoints behind the pages that used to invent their own data.

Cases, Evidence, Intelligence, Reports and the Dashboard each rendered a hardcoded
array. On an ordinary admin tool that is a cosmetic debt. Here the fixtures were
plausible Arabic hate-speech terms, invented handles like `@hate_network`, and offence
counts, displayed on pages titled "Evidence" and "Intelligence Hub" — findings about
named accounts that no one had found. A convincing demonstration and a false
accusation look identical from the outside.

`/api/admin/health` was the same fault a layer down: it returned 42.5 items/second and
five healthy Facebook accounts from a fixed dictionary, on a system that had never
processed anything. Wiring the dashboard to it would only have moved the lie.

These tests assert two things: the endpoints count reality, and they are all behind
authentication. Empty is a correct answer here.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.core.settings import get_settings

TOKEN = "test-admin-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

ENDPOINTS = [
    "/api/cases",
    "/api/evidence",
    "/api/intelligence/offenders",
    "/api/intelligence/trends",
    "/api/admin/health",
    "/api/admin/config",
]


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    monkeypatch.setenv("RUN_AGENT_WITH_API", "false")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import init_db

    await init_db()

    from src.api.main import app

    with TestClient(app) as c:
        yield c

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_dashboard_endpoint_requires_a_token(client, path):
    """These carry names, URLs and verdicts about people who are already targets.
    Auth is applied at router inclusion so a new endpoint is protected by default;
    this fails if one is ever added outside that."""
    assert client.get(path).status_code in (401, 403)


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_dashboard_endpoint_answers(client, path):
    assert client.get(path, headers=AUTH).status_code == 200


def test_an_empty_system_reports_empty_rather_than_a_demonstration(client):
    """The whole point. Nothing collected means nothing shown."""
    assert client.get("/api/cases", headers=AUTH).json()["cases"] == []
    assert client.get("/api/evidence", headers=AUTH).json()["evidence"] == []
    assert client.get("/api/intelligence/offenders", headers=AUTH).json()["offenders"] == []


def test_health_counts_instead_of_asserting(client):
    body = client.get("/api/admin/health", headers=AUTH).json()

    assert body["classified_last_hour"] == 0
    assert body["status"] == "idle", "a system doing nothing should not read as healthy"
    assert body["queue_depths"]["classification"] == 0
    assert body["account_health"] == {}
    # The old stub's tell: these exact numbers came back regardless of the database.
    assert body.get("crawl_throughput") != 42.5


def test_health_says_whether_verdicts_are_being_held(client):
    """A pending count with no explanation reads as a broken submission path."""
    body = client.get("/api/admin/health", headers=AUTH).json()

    assert body["outbox"]["verdicts_held"] is True
    assert body["platform_configured"] is False


# ── the settings page ────────────────────────────────────────────────────────


def test_config_exposes_only_the_allowlist(client):
    keys = {row["key"] for row in client.get("/api/admin/config", headers=AUTH).json()["settings"]}

    from src.chat.tools import SETTABLE_KEYS

    assert keys == set(SETTABLE_KEYS)


def test_no_credential_is_readable_through_config(client):
    body = client.get("/api/admin/config", headers=AUTH).json()
    keys = {row["key"] for row in body["settings"]}

    for secret in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "ADMIN_API_TOKEN",
        "ETTOK_AGENT_KEY",
        "SECRET_KEY",
        "TELEGRAM_BOT_TOKEN",
    ):
        assert secret not in keys
    assert TOKEN not in str(body), "a credential value leaked into the response"


def test_an_unknown_setting_is_refused(client):
    response = client.patch(
        "/api/admin/config", headers=AUTH, json={"key": "MADE_UP_KEY", "value": "1"}
    )
    assert response.status_code == 400


def test_a_credential_cannot_be_written_through_config(client):
    """The allowlist is the boundary; this fails loudly if it stops being one."""
    response = client.patch(
        "/api/admin/config", headers=AUTH, json={"key": "OPENAI_API_KEY", "value": "sk-x"}
    )
    assert response.status_code == 400


def test_an_invalid_value_is_refused_before_it_is_written(client):
    """A threshold outside 0..1 would be accepted into .env and crash the next cycle
    that reads it."""
    response = client.patch(
        "/api/admin/config", headers=AUTH, json={"key": "AUTO_FLAG_THRESHOLD", "value": "5"}
    )
    assert response.status_code == 400

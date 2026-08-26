"""The dashboard routes are actually requested here.

A `NameError: name 'FileResponse' is not defined` reached the operator's browser as
a 500. The suite was green because nothing in it had ever issued `GET /` — the
security tests cover /api/*, and the earlier mount-based implementation was verified
by hand and then rewritten. Any handler nothing requests is untested, however many
tests pass.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import _DIST, app

DASHBOARD_BUILT = (_DIST / "index.html").exists()
needs_build = pytest.mark.skipif(
    not DASHBOARD_BUILT, reason="frontend/dist not built in this checkout"
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@needs_build
def test_the_root_serves_the_dashboard(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


@needs_build
def test_a_client_side_route_serves_the_app(client):
    """A refresh on /cases asks the server for /cases, which is not a file."""
    response = client.get("/cases")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_an_unbuilt_dashboard_explains_itself(client):
    """503 naming the build command beats a bare 404 at the URL the CLI printed."""
    if DASHBOARD_BUILT:
        pytest.skip("dist is present, so the placeholder route is not registered")

    response = client.get("/")

    assert response.status_code == 503
    assert "npm run build" in response.json()["fix"]


# ── The dashboard must not swallow the API ───────────────────────────────────


def test_health_still_answers(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_the_api_docs_are_not_shadowed(client):
    assert client.get("/docs").status_code == 200


@pytest.mark.parametrize("path", ["/api/notifications", "/api/reports/summary"])
def test_an_api_path_still_requires_auth(client, path):
    """Mounting the SPA at "/" once returned the dashboard with 200 for these."""
    assert client.get(path).status_code in (401, 403, 503)


def test_an_unknown_api_path_is_not_answered_with_html(client):
    """A mistyped endpoint must not look like it worked."""
    response = client.get("/api/definitely-not-a-route")

    assert response.status_code != 200
    assert "text/html" not in response.headers.get("content-type", "")

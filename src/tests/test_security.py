"""API authentication and credential encryption.

Both were entirely absent: every router was open and `encrypted_credentials` held
plaintext JSON. These tests exist so a regression is loud, because the failure mode is
silent — an open endpoint and a plaintext password both look completely normal.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.core.settings import get_settings

TOKEN = "test-admin-token-that-is-long-enough"
SECRET = "a" * 48


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    monkeypatch.setenv("SECRET_KEY", SECRET)
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    import importlib

    import src.api.main as main

    importlib.reload(main)
    with TestClient(main.app) as client:
        yield client

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


PROTECTED = [
    "/api/review/queue",
    "/api/accounts/",
    "/api/notifications",
    "/api/reports/summary",
]


@pytest.mark.parametrize("path", PROTECTED)
def test_endpoints_reject_unauthenticated_requests(api, path):
    assert api.get(path).status_code == 401


@pytest.mark.parametrize("path", PROTECTED)
def test_endpoints_reject_a_wrong_token(api, path):
    response = api.get(path, headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_a_valid_token_is_accepted(api):
    """Not 401 — the endpoint may still 404 or 500 on empty data, which is fine here."""
    response = api.get("/api/review/queue", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code != 401


def test_health_stays_open_for_monitoring(api):
    assert api.get("/health").status_code == 200


def test_api_fails_closed_without_a_configured_token(tmp_path, monkeypatch):
    """An unfinished setup must not leave a silently open dashboard."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'api2.db'}")
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    monkeypatch.setenv("ADMIN_API_TOKEN", "")
    get_settings.cache_clear()

    import importlib

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None
    import src.api.main as main

    importlib.reload(main)

    with TestClient(main.app) as client:
        assert client.get("/api/review/queue").status_code == 503

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


def test_cors_is_not_a_wildcard(api):
    """`*` with credentials lets any site read the dashboard through a logged-in browser."""
    from src.core.settings import get_settings as fresh

    assert "*" not in fresh().cors_origins


# ------------------------------------------------------------- encryption


@pytest.fixture
def crypto_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_round_trip(crypto_env):
    from src.core.crypto import decrypt, encrypt

    assert decrypt(encrypt("hunter2")) == "hunter2"


def test_ciphertext_does_not_contain_the_plaintext(crypto_env):
    from src.core.crypto import encrypt

    assert "hunter2" not in encrypt("hunter2")


def test_encryption_is_not_applied_twice(crypto_env):
    from src.core.crypto import encrypt

    once = encrypt("token")
    assert encrypt(once) == once


def test_tampering_is_detected(crypto_env):
    """Fernet is authenticated — altered ciphertext fails rather than decrypting to junk."""
    from src.core.crypto import CryptoError, decrypt, encrypt

    blob = encrypt("secret")
    tampered = blob[:-4] + ("aaaa" if not blob.endswith("aaaa") else "bbbb")
    with pytest.raises(CryptoError):
        decrypt(tampered)


def test_legacy_plaintext_still_reads(crypto_env):
    """An upgrade must not lock the operator out of credentials written before this."""
    from src.core.crypto import decrypt

    assert decrypt('{"token": "abc"}') == '{"token": "abc"}'


def test_a_short_secret_is_refused(monkeypatch):
    from src.core.crypto import CryptoError, encrypt

    monkeypatch.setenv("SECRET_KEY", "tooshort")
    get_settings.cache_clear()
    with pytest.raises(CryptoError, match="32"):
        encrypt("x")
    get_settings.cache_clear()


def test_a_missing_secret_is_refused(monkeypatch):
    from src.core.crypto import CryptoError, encrypt

    monkeypatch.setenv("SECRET_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(CryptoError, match="SECRET_KEY"):
        encrypt("x")
    get_settings.cache_clear()

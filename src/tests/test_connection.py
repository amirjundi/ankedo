"""Connecting the agent to the platform: key validation and rotation.

Both exist because of the same operational reality — an NGO operator sets this up
once, on a laptop, possibly over a bad connection. A key that fails silently at setup
becomes a scan that fails at 3am, and rotation that requires downtime is rotation that
never happens.
"""
from __future__ import annotations

import httpx
import pytest

from src.core.settings import get_settings

TOKEN = "agent-key-abcdef"


@pytest.fixture(autouse=True)
def platform_env(monkeypatch):
    monkeypatch.setenv("ETTOK_BASE_URL", "https://example.test/api/hermes/")
    monkeypatch.setenv("ETTOK_AGENT_KEY", TOKEN)
    monkeypatch.setenv("ETTOK_MAX_RETRIES", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(handler):
    from src.ettok.client import EttokClient

    return EttokClient(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------ setup validation


@pytest.mark.parametrize(
    "status,fragment",
    [
        (401, "rejected"),
        (403, "scope"),
        (500, "platform error"),
    ],
)
def test_setup_names_the_specific_failure(monkeypatch, status, fragment):
    """A rejected key, an unreachable host and a missing scope need different fixes."""
    from src.cli import setup_wizard

    def fake_post(*args, **kwargs):
        return httpx.Response(status, request=httpx.Request("POST", "https://x/"))

    monkeypatch.setattr(httpx, "post", fake_post)
    ok, detail = setup_wizard._validate_agent_key("https://x/api/hermes/", "k", "agent")

    assert ok is False
    assert fragment in detail


def test_setup_reports_an_unreachable_host(monkeypatch):
    from src.cli import setup_wizard

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(httpx, "post", fake_post)
    ok, detail = setup_wizard._validate_agent_key("https://x/api/hermes/", "k", "agent")

    assert ok is False
    assert "could not reach" in detail


def test_setup_accepts_a_working_key(monkeypatch):
    from src.cli import setup_wizard

    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: httpx.Response(200, json={}, request=httpx.Request("POST", "https://x/")),
    )
    assert setup_wizard._validate_agent_key("https://x/api/hermes/", "k", "agent")[0] is True


def test_agent_id_defaults_to_something_machine_specific():
    """A shared default means the platform cannot tell two agents apart."""
    from src.cli.setup_wizard import _default_agent_id

    agent_id = _default_agent_id()
    assert agent_id.startswith("ankedo-")
    assert len(agent_id) > len("ankedo-")


# ------------------------------------------------------------------- rotation


async def test_heartbeat_adopts_a_rotated_key(tmp_path, monkeypatch):
    monkeypatch.setattr("src.ettok.client._persist_key", lambda key: None)

    seen: list[str] = []

    def handler(request):
        seen.append(request.headers["authorization"])
        if len(seen) == 1:
            return httpx.Response(200, json={"rotate_key": "new-key-999"})
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        await client.heartbeat()
        assert client.agent_key == "new-key-999"
        await client.heartbeat()

    assert seen[0] == f"Bearer {TOKEN}"
    assert seen[1] == "Bearer new-key-999", "the next call must use the rotated key"


async def test_rotation_is_written_to_env(tmp_path, monkeypatch):
    """In-memory only would revert to a revoked key on the next restart."""
    env = tmp_path / ".env"
    env.write_text(f"GEMINI_API_KEY=x\nETTOK_AGENT_KEY={TOKEN}\nAPI_PORT=8000\n", encoding="utf-8")

    import src.ettok.client as module

    monkeypatch.setattr(module, "_persist_key", lambda key: _write(env, key))

    def _write(path, key):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.startswith("ETTOK_AGENT_KEY="):
                lines[i] = f"ETTOK_AGENT_KEY={key}"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async with _client(lambda r: httpx.Response(200, json={"rotate_key": "rotated-42"})) as client:
        await client.heartbeat()

    content = env.read_text(encoding="utf-8")
    assert "ETTOK_AGENT_KEY=rotated-42" in content
    assert "GEMINI_API_KEY=x" in content, "other settings must survive untouched"


async def test_no_rotation_field_leaves_the_key_alone(monkeypatch):
    monkeypatch.setattr("src.ettok.client._persist_key", lambda key: None)

    async with _client(lambda r: httpx.Response(200, json={"scan_requested": True})) as client:
        await client.heartbeat()
        assert client.agent_key == TOKEN


async def test_rotation_to_the_same_key_is_a_no_op(monkeypatch):
    calls = []
    monkeypatch.setattr("src.ettok.client._persist_key", lambda key: calls.append(key))

    async with _client(lambda r: httpx.Response(200, json={"rotate_key": TOKEN})) as client:
        await client.heartbeat()

    assert calls == [], "no write when the key has not actually changed"


async def test_a_failed_env_write_does_not_break_the_run(monkeypatch):
    """The new key works in memory; the operator is warned before the window closes."""
    def boom(key):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("src.ettok.client._persist_key", boom)

    async with _client(lambda r: httpx.Response(200, json={"rotate_key": "new"})) as client:
        await client.heartbeat()
        assert client.agent_key == "new"


def test_persist_key_preserves_the_rest_of_env(tmp_path, monkeypatch):
    """Rewrites one line. An operator's other settings must survive a rotation."""
    from src.ettok import client as module

    project_root = tmp_path
    env = project_root / ".env"
    env.write_text(
        "\n".join(["A=1", "ETTOK_AGENT_KEY=old", "# a comment", "B=2"]) + "\n",
        encoding="utf-8",
    )

    # _persist_key walks up from its own file to the project root; point that at tmp.
    monkeypatch.setattr(module, "__file__", str(project_root / "src" / "ettok" / "client.py"))
    module._persist_key("brand-new")

    content = env.read_text(encoding="utf-8")
    assert "ETTOK_AGENT_KEY=brand-new" in content
    assert "ETTOK_AGENT_KEY=old" not in content
    assert "A=1" in content and "B=2" in content and "# a comment" in content


def test_persist_key_appends_when_absent(tmp_path, monkeypatch):
    from src.ettok import client as module

    env = tmp_path / ".env"
    env.write_text("A=1\n", encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(tmp_path / "src" / "ettok" / "client.py"))
    module._persist_key("fresh")

    assert "ETTOK_AGENT_KEY=fresh" in env.read_text(encoding="utf-8")

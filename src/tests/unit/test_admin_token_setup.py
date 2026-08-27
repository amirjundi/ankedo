"""Setup must produce a dashboard that can actually be opened.

ADMIN_API_TOKEN existed in exactly two places: the settings model that declares it and
the auth check that refuses every request without it. Not in the wizard, not in
.env.example, not in the docs, not in the README. So a fresh install served a
dashboard that rejected everything with "run `ankedo setup`" — and running setup did
not create it. The error and the fix pointed at each other, and the only way out was
to read the source.

The agent refusing to serve unauthenticated is right: it holds verdicts about named
people who are already targets. The bug was never generating the credential that
refusal requires.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_the_wizard_generates_an_admin_token():
    source = (ROOT / "src" / "cli" / "setup_wizard.py").read_text(encoding="utf-8")

    assert "ADMIN_API_TOKEN" in source, (
        "setup does not create ADMIN_API_TOKEN, so the dashboard it configures "
        "refuses every request"
    )


def test_both_setup_paths_generate_it():
    """The wizard has an interactive path and a non-interactive one for environment
    variables. Only generating it in one leaves the other broken."""
    source = (ROOT / "src" / "cli" / "setup_wizard.py").read_text(encoding="utf-8")

    assert source.count('config["ADMIN_API_TOKEN"] = secrets.token_urlsafe') == 2


def test_it_is_documented_where_an_operator_would_look():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert "ADMIN_API_TOKEN" in example


def test_the_error_message_names_a_command_that_creates_it():
    """It said "run `ankedo setup`", which did not create the token. An instruction
    that does not fix the problem is worse than none — it costs the operator the time
    to follow it before they start doubting it."""
    auth = (ROOT / "src" / "api" / "auth.py").read_text(encoding="utf-8")
    cli = (ROOT / "src" / "cli" / "__main__.py").read_text(encoding="utf-8")

    assert "ankedo token" in auth, "the 503 does not tell the operator what to run"
    assert 'name="token"' in cli, "`ankedo token` is advertised but does not exist"


def test_the_token_command_can_show_and_rotate(tmp_path, monkeypatch):
    """Show by default, rotate only when asked. A command that silently issued a new
    token every time it was run would sign out every open dashboard."""
    cli = (ROOT / "src" / "cli" / "__main__.py").read_text(encoding="utf-8")

    assert '"--new"' in cli
    assert "rotate" in cli


def test_the_agent_still_refuses_when_no_token_is_set(monkeypatch):
    """The fix is to generate the credential, never to drop the requirement."""
    from src.core.settings import get_settings

    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    get_settings.cache_clear()

    assert get_settings().admin_api_token is None

    auth = (ROOT / "src" / "api" / "auth.py").read_text(encoding="utf-8")
    assert "503" in auth or "SERVICE_UNAVAILABLE" in auth

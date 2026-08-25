"""The .env writer and the `configure set` path.

Regression cover for the bug that made a successful-looking setup produce an
unconfigured agent: _write_env rebuilt .env from .env.example and dropped every key
the template did not already mention — GEMINI_API_KEY and all three ETTOK_* keys
among them.
"""
from __future__ import annotations

import pytest

from src.cli import setup_wizard as wiz


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point the wizard at a throwaway .env / .env.example pair."""
    example = tmp_path / ".env.example"
    example.write_text(
        "# comment kept\n"
        "TRIAGE_MODEL=gemini-3.5-flash-lite\n"
        "\n"
        "LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )
    target = tmp_path / ".env"
    monkeypatch.setattr(wiz, "ENV_EXAMPLE", example)
    monkeypatch.setattr(wiz, "ENV_FILE", target)
    return target


def test_keys_absent_from_the_template_are_kept(env):
    wiz._write_env({"TRIAGE_MODEL": "gemini-3.6-flash", "GEMINI_API_KEY": "AIzaSECRET"})

    written = wiz._load_existing_env()
    assert written["TRIAGE_MODEL"] == "gemini-3.6-flash"  # overrides the template
    assert written["GEMINI_API_KEY"] == "AIzaSECRET"      # was silently dropped before
    assert written["LOG_LEVEL"] == "INFO"                 # template default survives
    assert "# comment kept" in env.read_text(encoding="utf-8")


def test_ettok_credentials_survive_a_round_trip(env):
    """Step 5 collected these and _write_env threw all three away."""
    wiz._write_env(
        {
            "ETTOK_BASE_URL": "https://ettok.net/api/hermes/",
            "ETTOK_AGENT_KEY": "key-123",
            "ETTOK_AGENT_ID": "ankedo-thinkpad",
        }
    )
    written = wiz._load_existing_env()
    assert written["ETTOK_AGENT_KEY"] == "key-123"
    assert written["ETTOK_BASE_URL"] == "https://ettok.net/api/hermes/"


def test_configure_set_updates_one_key_and_leaves_the_rest(env):
    wiz._write_env({"TRIAGE_MODEL": "gemini-3.5-flash-lite", "GEMINI_API_KEY": "AIzaSECRET"})

    wiz.set_env_values(("SPECIALIST_MODEL=gemini-3.6-flash",))

    written = wiz._load_existing_env()
    assert written["SPECIALIST_MODEL"] == "gemini-3.6-flash"
    assert written["GEMINI_API_KEY"] == "AIzaSECRET"
    assert written["TRIAGE_MODEL"] == "gemini-3.5-flash-lite"


def test_configure_set_rejects_a_pair_without_equals(env):
    wiz._write_env({"TRIAGE_MODEL": "gemini-3.5-flash-lite"})

    with pytest.raises(SystemExit):
        wiz.set_env_values(("SPECIALIST_MODEL",))


def test_every_model_role_has_a_default(env):
    """A role in MODEL_ENV_KEYS with no provider default writes an empty model id."""
    for role in wiz.MODEL_ENV_KEYS:
        assert role in wiz.PROVIDERS["gemini"]["models"]

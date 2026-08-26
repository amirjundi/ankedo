"""Inline comments in .env / .env.example are comments, not values.

`.env.example` documents nearly every key with a trailing comment. Taking everything
after the `=` made those comments the values: setup reported Telegram and WhatsApp as
configured because "# From @BotFather" is a non-empty string, wrote it to .env, and
pydantic loaded it as the bot token. Every model id was corrupted the same way —
TRIAGE_MODEL became "gemini-3.5-flash-lite       # Fast, cheap ...", which no API
accepts.
"""
from __future__ import annotations

import pytest

from src.cli import setup_wizard as wiz
from src.cli.setup_wizard import _parse_value


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(wiz, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(wiz, "ENV_EXAMPLE", tmp_path / ".env.example")
    return tmp_path


# ── The parser ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("gemini-3.6-flash        # Deep analysis", "gemini-3.6-flash"),
        ("                     # From @BotFather", ""),
        ("INFO   # DEBUG, INFO, WARNING, ERROR", "INFO"),
        ("plain-value", "plain-value"),
        ("", ""),
    ],
)
def test_a_trailing_comment_is_not_part_of_the_value(raw, expected):
    assert _parse_value(raw) == expected


def test_a_hash_without_leading_whitespace_is_kept():
    """A password or URL fragment may legitimately contain one."""
    assert _parse_value("p4ss#word!") == "p4ss#word!"
    assert _parse_value("https://example.com/page#section") == "https://example.com/page#section"


def test_a_quoted_value_is_taken_whole():
    """The escape hatch for a value that really does start with a hash."""
    assert _parse_value('"# literally this"') == "# literally this"
    assert _parse_value("'  spaced  '") == "  spaced  "


# ── The symptom the operator saw ─────────────────────────────────────────────


def test_setup_does_not_report_unconfigured_channels_as_configured(env):
    (env / ".env.example").write_text(
        "TELEGRAM_BOT_TOKEN=                     # From @BotFather\n"
        "WHATSAPP_ACCESS_TOKEN=                  # Permanent/system user token\n",
        encoding="utf-8",
    )

    wiz._write_env({"GEMINI_API_KEY": "AIzaREAL"})
    written = wiz._load_existing_env()

    # Truthiness is exactly what the summary table tested, so empty is the fix.
    assert not written["TELEGRAM_BOT_TOKEN"]
    assert not written["WHATSAPP_ACCESS_TOKEN"]


def test_model_ids_survive_a_round_trip_uncorrupted(env):
    (env / ".env.example").write_text(
        "TRIAGE_MODEL=gemini-3.5-flash-lite       # Fast, cheap\n"
        "LOG_LEVEL=INFO                          # DEBUG, INFO, WARNING, ERROR\n",
        encoding="utf-8",
    )

    wiz._write_env({})
    written = wiz._load_existing_env()

    assert written["TRIAGE_MODEL"] == "gemini-3.5-flash-lite"
    assert written["LOG_LEVEL"] == "INFO"


def test_a_comma_separated_list_is_not_polluted_by_its_comment(env):
    """MCP_SERVERS and the proxy list are split on commas downstream."""
    (env / ".env.example").write_text(
        "MCP_SERVERS=                            # Comma-separated: tavily_search,memory\n",
        encoding="utf-8",
    )

    wiz._write_env({})

    assert wiz._load_existing_env()["MCP_SERVERS"] == ""


def test_the_real_template_yields_no_commented_values():
    """Guard the actual shipped .env.example, not just a fixture."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[3] / ".env.example"
    bad = []
    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        if _parse_value(raw).startswith("#"):
            bad.append(key.strip())

    assert bad == [], f"these keys would take a comment as their value: {bad}"

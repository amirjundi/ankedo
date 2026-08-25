"""`ankedo doctor` must check the key for the provider actually selected.

The old check passed if any provider's key was present, so LLM_PROVIDER=gemini with
only an OPENAI_API_KEY set reported healthy and failed on the first classification.
"""
from __future__ import annotations

import pytest

from src.cli import health_check as hc


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    target = tmp_path / ".env"
    monkeypatch.setattr(hc, "ENV_FILE", target)

    def write(**pairs):
        target.write_text(
            "# a comment\n" + "\n".join(f"{k}={v}" for k, v in pairs.items()) + "\n",
            encoding="utf-8",
        )

    return write


def test_gemini_selected_with_only_an_openai_key_fails(env_file):
    env_file(LLM_PROVIDER="gemini", OPENAI_API_KEY="sk-" + "x" * 30)

    check = hc._check_api_key()

    assert check.status == "fail"
    assert "GEMINI_API_KEY" in check.detail


def test_gemini_selected_with_its_key_passes(env_file):
    env_file(LLM_PROVIDER="gemini", GEMINI_API_KEY="AIza" + "x" * 30)
    assert hc._check_api_key().status == "pass"


def test_openai_selected_reports_the_endpoint(env_file):
    env_file(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-" + "x" * 30,
        OPENAI_BASE_URL="http://localhost:11434/v1",
    )

    check = hc._check_api_key()

    assert check.status == "pass"
    assert "localhost:11434" in check.detail


def test_provider_defaults_to_gemini_when_unset(env_file):
    """An .env predating LLM_PROVIDER still has to be judged against something."""
    env_file(GEMINI_API_KEY="AIza" + "x" * 30)
    assert hc._check_api_key().status == "pass"


def test_an_empty_key_is_not_a_configured_key(env_file):
    env_file(LLM_PROVIDER="gemini", GEMINI_API_KEY="")
    assert hc._check_api_key().status == "fail"


def test_an_unknown_provider_is_reported(env_file):
    env_file(LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-" + "x" * 30)

    check = hc._check_api_key()

    assert check.status == "fail"
    assert "anthropic" in check.detail

"""Drive the wizard end to end with scripted answers.

The MarkupError that stopped setup on the operator's machine was in a print
statement that only executes when someone reaches Step 1 and looks at the provider
menu. Nothing in the suite had ever run the wizard, so the crash was found by the
person trying to install it.

These answer the prompts and let every console.print actually render.
"""
from __future__ import annotations

import pytest

from src.cli import setup_wizard as wiz


@pytest.fixture
def wizard(tmp_path, monkeypatch):
    """run_setup with a throwaway .env, no network, and no database."""
    example = tmp_path / ".env.example"
    example.write_text(
        "GEMINI_API_KEY=AIza...\nOPENAI_API_KEY=\nLLM_PROVIDER=gemini\n"
        "TRIAGE_MODEL=gemini-3.5-flash-lite\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(wiz, "ENV_EXAMPLE", example)
    monkeypatch.setattr(wiz, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(wiz, "PROJECT_ROOT", tmp_path)

    # The guard that refuses to prompt without a terminal; the answers are scripted.
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(wiz, "_validate_api_key", lambda *a, **k: True)
    monkeypatch.setattr(wiz, "_validate_agent_key", lambda *a, **k: (True, "ok"))

    def run(prompts, confirms):
        p, c = list(prompts), list(confirms)
        monkeypatch.setattr(
            wiz.Prompt, "ask", staticmethod(lambda *a, **k: p.pop(0) if p else (k.get("default") or ""))
        )
        monkeypatch.setattr(
            wiz.Confirm, "ask", staticmethod(lambda *a, **k: c.pop(0) if c else False)
        )
        wiz.run_setup()
        return wiz._load_existing_env()

    return run


def test_the_gemini_path_completes_and_writes_a_key(wizard):
    config = wizard(
        prompts=["1", "AIzaTESTKEY0123456789012345678901"],
        confirms=[
            False,  # customise model assignments?
            False,  # telegram
            False,  # whatsapp
            False,  # connect to a platform now?
            True,   # save
        ],
    )

    assert config["LLM_PROVIDER"] == "gemini"
    assert config["GEMINI_API_KEY"] == "AIzaTESTKEY0123456789012345678901"
    assert config["TRIAGE_MODEL"].startswith("gemini")
    assert config["SECRET_KEY"], "a secret key must be generated"


def test_the_openai_path_writes_the_endpoint_and_switches_models(wizard):
    config = wizard(
        prompts=[
            "2",                      # OpenAI-compatible
            "6",                      # Ollama
            "http://localhost:11434/v1",
            "sk-testtesttesttesttest",
        ],
        confirms=[False, False, False, False, True],
    )

    assert config["LLM_PROVIDER"] == "openai"
    assert config["OPENAI_BASE_URL"] == "http://localhost:11434/v1"
    # Switching provider must not leave gemini model ids behind.
    assert not config["SPECIALIST_MODEL"].startswith("gemini")


def test_declining_to_save_writes_nothing(wizard):
    wizard(
        prompts=["1", "AIzaTESTKEY0123456789012345678901"],
        confirms=[False, False, False, False, False],
    )

    assert not wiz.ENV_FILE.exists()


def test_customising_models_records_every_role(wizard):
    config = wizard(
        prompts=[
            "1", "AIzaTESTKEY0123456789012345678901",
            "m-triage", "m-specialist", "m-critic", "m-group", "m-vision", "m-chat",
        ],
        confirms=[True, False, False, False, True],
    )

    assert config["TRIAGE_MODEL"] == "m-triage"
    assert config["VISION_MODEL"] == "m-vision"
    assert config["TARGET_GROUP_MODEL"] == "m-group"


def test_the_wizard_refuses_without_a_terminal(monkeypatch, tmp_path):
    """The EOF loop that ended in Aborted! under `curl | bash`."""
    monkeypatch.setattr(wiz, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    with pytest.raises(SystemExit):
        wiz.run_setup()

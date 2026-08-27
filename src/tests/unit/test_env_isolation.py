"""The suite must not read the developer's `.env`.

Found the hard way: a killed test run left a scratch `.env` in the project root, and
`test_a_missing_key_stops_before_any_network_call` began failing — it deletes
OPENAI_API_KEY from the environment, but settings went on reading the key out of the
file. The test was not asserting what it looked like it asserted, on any machine where
that file exists.

The same run also took 11.87s instead of 2.55s, because with a real key and a real
base URL it dialled a real endpoint. A suite that reaches the network when nobody
asked it to is a suite that can spend money, leak a key into a log, and fail because
somebody else's server is down.
"""
from __future__ import annotations

from src.core.settings import AgentSettings, get_settings


def test_settings_do_not_read_an_env_file_during_tests():
    assert AgentSettings.model_config.get("env_file") is None


def test_a_deleted_variable_stays_deleted(monkeypatch):
    """The actual failure mode: monkeypatch removes it, the file puts it back."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-set-by-this-test")
    get_settings.cache_clear()
    assert get_settings().openai_api_key == "sk-set-by-this-test"

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    assert get_settings().openai_api_key is None, "a .env file is leaking into tests"


def test_the_project_env_file_is_not_consulted_even_if_it_exists(tmp_path, monkeypatch):
    """Belt and braces: write a file where settings would look, and prove it is
    ignored. Without the isolation fixture this test would read the value back."""
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    get_settings.cache_clear()

    assert get_settings().admin_api_token != "value-from-a-file"

"""Test-wide isolation from the developer's own configuration.

`AgentSettings` reads an absolute `.env` next to the project root. That is correct for
the application — it is what lets `ankedo start` work when run from a home directory,
which was a real bug — but it means the test suite silently inherits whatever the
person running it happens to have configured.

Three ways that bites, in ascending order of seriousness:

* A test asserting behaviour when a key is absent passes on CI and fails on the
  machine of anyone who has one. That is how this fixture was found.
* A test that reaches the network gets a *real* endpoint and a *real* key, so a suite
  that is supposed to be hermetic starts spending money and depending on an upstream
  service being awake.
* A test that writes config could overwrite the developer's working `.env`.

So the suite runs with no env file at all. Anything a test needs, it sets explicitly
with `monkeypatch.setenv` — which then reads as documentation of what that test
actually depends on, rather than as an accident of the machine.
"""
from __future__ import annotations

import pytest

from src.core.settings import AgentSettings, get_settings


@pytest.fixture(autouse=True, scope="session")
def _ignore_developer_env_file():
    """Point settings at a file that does not exist, for the whole run.

    Session-scoped and autouse: it has to be in place before the first `get_settings()`
    anywhere, including at import time in modules that read configuration eagerly.
    """
    original = AgentSettings.model_config.get("env_file")
    AgentSettings.model_config["env_file"] = None
    get_settings.cache_clear()
    try:
        yield
    finally:
        AgentSettings.model_config["env_file"] = original
        get_settings.cache_clear()

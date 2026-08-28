"""Setup must leave a fallback chain behind.

The fallback machinery was built, wired into every model call, and never populated.
FALLBACK_MODELS defaulted to empty and the wizard never wrote it, so one 429 ended the
turn — while four other models the same provider served sat there able to answer.

The operator hit exactly this: "Model nemotron-3-super-free is not supported (free
model rate limit)", on a proxy serving five models where most are rate-limited most of
the time. On a free tier a 429 is the normal case, not the exception, and an agent
that gives up on the first one is an agent that mostly does not work.
"""
from __future__ import annotations

import pytest

from src.cli.setup_wizard import MODEL_ENV_KEYS, _set_fallback_chain

AVAILABLE = [
    "big-pickle",
    "deepseek-v4-flash-free",
    "minimax-m2.5-free",
    "nemotron-3-super-free",
    "qwen3.6-plus-free",
]


def _config(**over):
    base = {env_key: "big-pickle" for env_key, _ in MODEL_ENV_KEYS.values()}
    base.update(over)
    return base


def test_a_chain_is_written():
    config = _config()
    _set_fallback_chain(config, AVAILABLE)

    assert config["FALLBACK_MODELS"], "one 429 will end the turn"


def test_the_primary_chat_model_is_not_in_its_own_chain():
    """It is what the call already tried. Retrying it first wastes the attempt that
    matters most — the one right after a rate limit."""
    config = _config(CHAT_AGENT_MODEL="qwen3.6-plus-free")
    _set_fallback_chain(config, AVAILABLE)

    assert "qwen3.6-plus-free" not in config["FALLBACK_MODELS"].split(",")


def test_models_the_operator_chose_come_first():
    """A model picked for triage and critic is one they consider good, and is a
    better second choice than one nothing selected."""
    config = _config(
        TRIAGE_MODEL="big-pickle",
        CRITIC_MODEL="big-pickle",
        CHAT_AGENT_MODEL="qwen3.6-plus-free",
        SPECIALIST_MODEL="minimax-m2.5-free",
    )
    _set_fallback_chain(config, AVAILABLE)

    assert config["FALLBACK_MODELS"].split(",")[0] == "big-pickle"


def test_an_existing_chain_is_left_alone():
    """An operator who set their own order has decided something."""
    config = _config(FALLBACK_MODELS="only-this-one")
    _set_fallback_chain(config, AVAILABLE)

    assert config["FALLBACK_MODELS"] == "only-this-one"


def test_a_single_model_provider_gets_no_chain():
    """A fallback list naming nothing is an empty gesture."""
    # Every role on the one model the provider serves — the real shape of a
    # single-model install, rather than roles pointing at models it does not have.
    config = {env_key: "solo" for env_key, _ in MODEL_ENV_KEYS.values()}
    _set_fallback_chain(config, ["solo"])

    assert not config.get("FALLBACK_MODELS")


def test_the_chain_is_bounded():
    """Every model on a large provider would turn one failed turn into dozens of
    upstream calls — the retry storm that took the operator's proxy down."""
    config = _config(CHAT_AGENT_MODEL="a")
    _set_fallback_chain(config, [f"m{i}" for i in range(40)])

    assert len(config["FALLBACK_MODELS"].split(",")) <= 4


def test_a_rate_limit_is_treated_as_transient():
    """The chain only helps if a 429 falls through to it. This is the operator's
    exact error string."""
    from src.classifiers.llm_client import _is_transient

    assert _is_transient(
        "Error code: 429 - {'error': {'message': 'Model nemotron-3-super-free is "
        "not supported (free model rate limit)', 'type': 'rate_limit_error'}}"
    )

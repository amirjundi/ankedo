"""Model discovery: ask the provider what it serves, do not assume.

The per-provider defaults are gpt-4o and friends. Against an OpenAI-compatible proxy
serving Llama or Qwen those names do not exist, so setup wrote a config whose every
call would 404 and offered no way to see the real list.
"""
from __future__ import annotations

import httpx
import pytest

from src.cli.setup_wizard import PROVIDERS, _can_chat, _size_of, _suggest, fetch_models

PROXY = [
    "llama-3.3-70b-instruct", "llama-3.2-3b-instruct", "qwen2.5-coder-32b",
    "deepseek-v3", "gemma-2-9b-it", "pixtral-12b-vision",
]


def _pick(role, available, provider="openai"):
    return _suggest(role, available, PROVIDERS[provider]["models"][role])


# ── Fetching ─────────────────────────────────────────────────────────────────


def _reply(url, payload, status=200):
    """A Response with its request set — raise_for_status needs one."""
    return httpx.Response(status, json=payload, request=httpx.Request("GET", url))


def test_an_openai_style_listing_is_read(monkeypatch):
    seen = {}

    def fake_get(url, **kw):
        seen["url"] = url
        seen["auth"] = (kw.get("headers") or {}).get("Authorization")
        return _reply(url, {"data": [{"id": "llama-3.3-70b"}, {"id": "qwen2.5"}]})

    monkeypatch.setattr(httpx, "get", fake_get)

    assert fetch_models("openai", "sk-x", "http://127.0.0.1:6446/v1") == ["llama-3.3-70b", "qwen2.5"]
    assert seen["url"] == "http://127.0.0.1:6446/v1/models"
    assert seen["auth"] == "Bearer sk-x"


def test_a_bare_list_response_is_accepted(monkeypatch):
    """Not every proxy wraps its listing in {"data": ...}."""
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _reply(url, [{"id": "local-model"}]))

    assert fetch_models("openai", "k", "http://localhost:1234/v1") == ["local-model"]


def test_gemini_lists_only_models_that_can_generate(monkeypatch):
    payload = {"models": [
        {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
    ]}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _reply(url, payload))

    assert fetch_models("gemini", "AIza") == ["gemini-3.6-flash"]


def test_an_endpoint_without_a_listing_returns_empty_rather_than_raising(monkeypatch):
    """A gateway that does not implement /models must not stop setup."""
    def boom(url, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", boom)

    assert fetch_models("openai", "k", "http://localhost:9999/v1") == []


def test_a_404_listing_returns_empty(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: _reply(url, {"error": "nope"}, status=404))

    assert fetch_models("openai", "k", "http://x/v1") == []


# ── Filtering ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model_id", ["text-embedding-3-small", "whisper-1", "dall-e-3", "tts-1", "bge-reranker"]
)
def test_models_that_cannot_answer_a_prompt_are_excluded(model_id):
    """An embedding model sorts first as the cheapest and would win triage."""
    assert not _can_chat(model_id)


@pytest.mark.parametrize("model_id", ["gpt-4o-mini", "llama-3.2-3b-instruct", "gemini-3.6-flash"])
def test_chat_models_are_kept(model_id):
    assert _can_chat(model_id)


# ── Ranking ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,expected",
    [("llama-3.3-70b-instruct", 70.0), ("llama-3.2-3b-instruct", 3.0),
     ("qwen-1.5b", 1.5), ("gpt-4o", None), ("gemini-3.6-flash", None)],
)
def test_parameter_count_is_read_from_the_name(name, expected):
    assert _size_of(name) == expected


def test_the_cheapest_model_is_chosen_for_the_per_item_roles():
    """Triage, critic and group resolution run on everything collected."""
    for role in ("triage", "critic", "target_group"):
        assert _pick(role, PROXY) == "llama-3.2-3b-instruct"


def test_the_most_capable_model_is_chosen_for_the_specialist():
    assert _pick("specialist", PROXY) == "deepseek-v3"


def test_a_vision_model_is_preferred_for_images():
    """An earlier heuristic matched '-v' and picked deepseek-v3, which cannot see."""
    assert _pick("vision", PROXY) == "pixtral-12b-vision"


def test_more_size_words_means_cheaper():
    """flash-lite is cheaper than flash; listing order must not decide it."""
    gemini = ["gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.6-pro"]

    assert _suggest("triage", gemini, "gemini-3.5-flash-lite") == "gemini-3.5-flash-lite"
    assert _suggest("specialist", gemini, "absent-model") == "gemini-3.6-pro"


def test_a_default_the_provider_serves_is_kept():
    assert _pick("specialist", ["gpt-4o", "gpt-4o-mini"]) == "gpt-4o"


def test_an_empty_listing_falls_back_to_the_provider_default():
    assert _pick("specialist", []) == PROVIDERS["openai"]["models"]["specialist"]


# ── Declared metadata beats the name heuristic ───────────────────────────────
#
# Read off openclaw/openclaw, which keeps a declared catalog per provider (id, name,
# input modalities, cost, context window) instead of inferring from ids, and only
# fetches live where the catalog is per-account. Our proxies cannot be catalogued in
# advance, so we fetch live and read whatever the endpoint declares.

from src.cli.setup_wizard import ModelInfo, _parse_openai_model, _suggest_from_catalog

OPENROUTER_ROWS = [
    {"id": "meta-llama/llama-3.2-3b-instruct", "name": "Llama 3.2 3B",
     "architecture": {"input_modalities": ["text"]}, "pricing": {"prompt": "0.015"}},
    {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Llama 3.3 70B",
     "architecture": {"input_modalities": ["text"]}, "pricing": {"prompt": "0.6"}},
    {"id": "mistralai/pixtral-12b", "name": "Pixtral 12B",
     "architecture": {"input_modalities": ["text", "image"]}, "pricing": {"prompt": "0.1"}},
]


def _catalog(rows=None):
    return [m for m in (_parse_openai_model(r) for r in (rows or OPENROUTER_ROWS)) if m]


def test_declared_modality_is_read():
    by_id = {m.id: m for m in _catalog()}

    assert by_id["mistralai/pixtral-12b"].vision is True
    assert by_id["meta-llama/llama-3.3-70b-instruct"].vision is False


def test_declared_price_is_read():
    assert _catalog()[0].cost_in is not None


def test_a_copilot_style_capability_block_is_read():
    model = _parse_openai_model(
        {"id": "gpt-4o", "object": "model",
         "capabilities": {"type": "chat", "supports": {"vision": True},
                          "limits": {"max_context_window_tokens": 128000}}}
    )

    assert model.vision is True
    assert model.context == 128000


def test_a_non_model_object_is_rejected():
    assert _parse_openai_model({"id": "router-x", "object": "router"}) is None
    assert _parse_openai_model({"id": "e5", "capabilities": {"type": "embedding"}}) is None


def test_price_decides_the_cheap_roles_not_the_name():
    """The name heuristic got this wrong twice; a declared price cannot."""
    catalog = _catalog()

    for role in ("triage", "critic", "target_group"):
        assert _suggest_from_catalog(role, catalog, "absent") == "meta-llama/llama-3.2-3b-instruct"
    assert _suggest_from_catalog("specialist", catalog, "absent") == "meta-llama/llama-3.3-70b-instruct"


def test_declared_vision_wins_over_the_name():
    """pixtral-12b is neither the largest nor named like a vision model here."""
    assert _suggest_from_catalog("vision", _catalog(), "absent") == "mistralai/pixtral-12b"


def test_the_cheapest_seeing_model_is_preferred():
    catalog = _catalog(OPENROUTER_ROWS + [
        {"id": "expensive/vision-xl", "architecture": {"input_modalities": ["text", "image"]},
         "pricing": {"prompt": "9.0"}},
    ])

    assert _suggest_from_catalog("vision", catalog, "absent") == "mistralai/pixtral-12b"


def test_an_endpoint_declaring_nothing_still_gets_the_heuristic():
    """A bare /v1/models has ids and nothing else — most local proxies."""
    catalog = [ModelInfo(id="llama-3.2-3b"), ModelInfo(id="llama-3.3-70b")]

    assert _suggest_from_catalog("triage", catalog, "absent") == "llama-3.2-3b"
    assert _suggest_from_catalog("specialist", catalog, "absent") == "llama-3.3-70b"

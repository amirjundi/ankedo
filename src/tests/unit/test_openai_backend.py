"""The OpenAI-compatible backend.

Covers the parts that are easy to get quietly wrong: strict-schema construction,
the fallback for gateways that reject json_schema, and token accounting on the
failure paths — a refusal still costs prompt tokens, and the budget ledger has to
see them or NFR-SC-2's cap drifts.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.classifiers import llm_client as mod
from src.classifiers.llm_client import LLMError, _build_backend, _OpenAIBackend


class Verdict(BaseModel):
    is_hate: bool
    confidence: float
    notes: str | None = None


# ── Fakes ────────────────────────────────────────────────────────────────────


def _response(content, *, refusal=None, prompt_tokens=11, completion_tokens=7,
              finish_reason="stop"):
    message = SimpleNamespace(content=content, refusal=refusal)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


class FakeCompletions:
    def __init__(self, responses, reject_json_schema=False):
        self._responses = list(responses)
        self.reject_json_schema = reject_json_schema
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.reject_json_schema and kwargs["response_format"]["type"] == "json_schema":
            raise RuntimeError("400: response_format json_schema is not supported")
        return self._responses.pop(0)


@pytest.fixture
def backend(monkeypatch):
    """An _OpenAIBackend whose transport is a fake; returns (backend, completions)."""
    def make(responses, reject_json_schema=False):
        completions = FakeCompletions(responses, reject_json_schema)
        client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
        monkeypatch.setattr(
            "openai.AsyncOpenAI", lambda **kwargs: client, raising=False
        )
        return _OpenAIBackend("sk-test", None), completions

    return make


async def _complete(be, **over):
    kwargs = dict(
        model="gpt-4o-mini",
        prompt="classify this",
        schema=Verdict,
        system_instruction=None,
        images=None,
    )
    kwargs.update(over)
    return await be.complete(**kwargs)


# ── Strict schema ────────────────────────────────────────────────────────────


def test_strict_schema_closes_objects_and_requires_every_field():
    """Strict mode rejects a schema with optional fields or open objects."""
    schema = _OpenAIBackend._strict_schema(Verdict)

    assert schema["additionalProperties"] is False
    # `notes` is Optional in the model but must still be listed as required.
    assert set(schema["required"]) == {"is_hate", "confidence", "notes"}


# ── Happy path ───────────────────────────────────────────────────────────────


async def test_parses_a_valid_response_and_reports_usage(backend):
    be, calls = backend([_response(json.dumps({"is_hate": True, "confidence": 0.9,
                                               "notes": "slur"}))])

    parsed, prompt_tokens, output_tokens = await _complete(be)

    assert parsed.is_hate is True
    assert parsed.confidence == 0.9
    assert (prompt_tokens, output_tokens) == (11, 7)
    assert calls.calls[0]["temperature"] == 0
    assert calls.calls[0]["seed"] == mod.DETERMINISTIC_SEED


async def test_images_are_sent_as_data_uris(backend):
    be, calls = backend([_response(json.dumps({"is_hate": False, "confidence": 0.1,
                                               "notes": None}))])

    await _complete(be, images=[b"\x89PNG-bytes"])

    content = calls.calls[0]["messages"][-1]["content"]
    image_parts = [p for p in content if p["type"] == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


# ── Fallback for gateways without json_schema ────────────────────────────────


async def test_falls_back_to_json_object_when_json_schema_is_rejected(backend):
    good = _response(json.dumps({"is_hate": False, "confidence": 0.2, "notes": None}))
    be, calls = backend([good], reject_json_schema=True)

    parsed, _, _ = await _complete(be)

    assert parsed.is_hate is False
    assert [c["response_format"]["type"] for c in calls.calls] == ["json_schema", "json_object"]
    # The schema has to reach the model somehow once the server stops enforcing it.
    assert "schema" in calls.calls[1]["messages"][0]["content"].lower()


async def test_fallback_is_remembered_so_the_retry_is_paid_once(backend):
    payload = json.dumps({"is_hate": False, "confidence": 0.2, "notes": None})
    be, calls = backend([_response(payload), _response(payload)], reject_json_schema=True)

    await _complete(be)
    await _complete(be)

    # First call probes then falls back; the second goes straight to json_object.
    assert [c["response_format"]["type"] for c in calls.calls] == [
        "json_schema", "json_object", "json_object",
    ]


async def test_an_unrelated_error_is_not_swallowed_as_a_format_problem(backend):
    class Boom(FakeCompletions):
        async def create(self, **kwargs):
            raise RuntimeError("401 invalid api key")

    completions = Boom([])
    import openai  # noqa: F401  — patched attribute must exist

    be, _ = backend([])
    be._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    with pytest.raises(RuntimeError, match="invalid api key"):
        await _complete(be)


# ── Failure paths still bill ─────────────────────────────────────────────────


async def test_a_refusal_carries_its_token_cost(backend):
    be, _ = backend([_response(None, refusal="I can't help with that",
                               prompt_tokens=120, completion_tokens=3)])

    with pytest.raises(LLMError) as caught:
        await _complete(be)

    assert caught.value.prompt_tokens == 120
    assert caught.value.output_tokens == 3


async def test_a_schema_mismatch_carries_its_token_cost(backend):
    """Two responses: a gateway that accepts json_schema without honouring it gets
    one retry through the prompt-level fallback before the error is raised."""
    bad = '{"wrong": "shape"}'
    # Three attempts before giving up: strict, the prompt-level fallback, then one
    # corrective pass that shows the model its own unparseable output.
    be, calls = backend([_response(bad, prompt_tokens=55) for _ in range(3)])

    with pytest.raises(LLMError) as caught:
        await _complete(be)

    assert caught.value.prompt_tokens == 55
    assert "Verdict" in str(caught.value)
    assert [c["response_format"]["type"] for c in calls.calls] == [
        "json_schema", "json_object", "json_object",
    ]


async def test_an_unparseable_reply_gets_one_corrective_attempt(backend):
    """Weak models often comply when told concretely what was wrong. Losing a turn
    to a stray sentence is the difference between a usable chat and an abandoned one."""
    good = json.dumps({"is_hate": False, "confidence": 0.1, "notes": None})
    be, calls = backend([_response("Sure!"), _response("Here you go:"), _response(good)])

    parsed, _, _ = await _complete(be)

    assert parsed.is_hate is False
    correction = calls.calls[-1]["messages"][-1]["content"][0]["text"]
    assert "could not be parsed" in correction
    assert "Here you go:" in correction, "the model was not shown its own output"


async def test_an_unhonoured_json_schema_is_retried_with_a_prompt_instruction(backend):
    """HTTP 200 is not evidence that structured output happened. This proxy returns
    200 for strict mode and the model answers in prose."""
    good = json.dumps({"is_hate": True, "confidence": 0.9, "notes": None})
    be, calls = backend([_response("Sure! Here is my analysis..."), _response(good)])

    parsed, _, _ = await _complete(be)

    assert parsed.is_hate is True
    assert len(calls.calls) == 2
    instruction = calls.calls[1]["messages"][-1]["content"][0]["text"]
    assert "ONLY a single JSON object" in instruction


# ── Backend selection ────────────────────────────────────────────────────────


def _settings(**over):
    base = dict(
        llm_provider="gemini",
        gemini_api_key=None,
        openai_api_key=None,
        openai_base_url=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_openai_provider_without_a_key_names_the_command_to_fix_it():
    with pytest.raises(LLMError, match="OPENAI_API_KEY"):
        _build_backend(_settings(llm_provider="openai"), None)


def test_gemini_provider_without_a_key_points_at_setup():
    with pytest.raises(LLMError, match="GEMINI_API_KEY"):
        _build_backend(_settings(), None)


def test_openai_provider_selects_the_openai_backend(monkeypatch):
    monkeypatch.setattr(
        "openai.AsyncOpenAI",
        lambda **kwargs: SimpleNamespace(base_url=kwargs.get("base_url")),
        raising=False,
    )
    be = _build_backend(
        _settings(llm_provider="openai", openai_api_key="sk-x",
                  openai_base_url="http://localhost:11434/v1"),
        None,
    )
    assert be.name == "openai"

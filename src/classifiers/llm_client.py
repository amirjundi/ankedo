"""LLM client for the classification committee.

Three things here are load-bearing and non-obvious.

**Safety filters must be disabled.** Gemini's defaults block hate speech — which is
precisely the content this system exists to read. Left on, the classifier receives an
empty response for exactly the items that matter most, and the failure looks like a
model that "didn't find anything". This is a content-moderation tool analysing
harmful speech to protect its targets; the filters are counterproductive here.

**Determinism.** FR-CL-13 requires results reproducible given a fixed model, prompt
version and dictionary version, so changes can be eval-gated. temperature=0 plus a
fixed seed, and every response carries the versions that produced it.

**Structured output.** Responses are parsed into Pydantic schemas rather than free
text, so a malformed answer fails loudly instead of being silently misread.

Two backends satisfy that contract. Gemini is the reference implementation. The
OpenAI backend targets any endpoint speaking /v1/chat/completions — OpenAI itself,
OpenRouter, Groq, Together, DeepSeek, Ollama, LM Studio — selected with
LLM_PROVIDER=openai and pointed with OPENAI_BASE_URL.

The two are not equivalent, and the difference matters for this workload:

- Gemini exposes per-category safety thresholds and this client sets them to
  BLOCK_NONE. OpenAI-compatible endpoints have no equivalent — moderation is baked
  into the model. Expect refusals on the most extreme items, which arrive here as
  LLMError rather than as a silent miss.
- `seed` is best-effort on OpenAI and ignored outright by most compatible gateways,
  so FR-CL-13 reproducibility is weaker off Gemini. The model and prompt versions are
  still recorded per response, so a drift is at least detectable after the fact.
"""
from __future__ import annotations

import base64
import json
import time
from typing import TypeVar

import structlog
from google import genai
from google.genai import types
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.budget import check_budget, record_call
from src.core.settings import get_settings

log = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)

# Fixed seed so identical input yields identical output where the model supports it.
DETERMINISTIC_SEED = 42

# The model must be able to read hate speech in order to classify it.
_UNFILTERED = [
    types.SafetySetting(category=category, threshold=types.HarmBlockThreshold.BLOCK_NONE)
    for category in (
        types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
    )
]


class LLMError(RuntimeError):
    """Model call failed, or returned something unparseable.

    Carries whatever usage the response reported: a refusal or a schema mismatch
    still spent its prompt tokens, and the budget ledger has to see them.
    """

    def __init__(self, message: str, *, prompt_tokens: int = 0, output_tokens: int = 0):
        super().__init__(message)
        self.prompt_tokens = prompt_tokens
        self.output_tokens = output_tokens


class _GeminiBackend:
    """google-genai: native structured output, seed, and disableable safety filters."""

    name = "gemini"

    def __init__(self, api_key: str):
        self._client = genai.Client(api_key=api_key)

    async def complete(
        self,
        *,
        model: str,
        prompt: str,
        schema: type[T],
        system_instruction: str | None,
        images: list[bytes] | None,
    ) -> tuple[T, int, int]:
        contents: list = [prompt]
        if images:
            contents.extend(
                types.Part.from_bytes(data=image, mime_type="image/png") for image in images
            )

        config = types.GenerateContentConfig(
            temperature=0,
            seed=DETERMINISTIC_SEED,
            response_mime_type="application/json",
            response_schema=schema,
            safety_settings=_UNFILTERED,
            system_instruction=system_instruction,
        )

        response = await self._client.aio.models.generate_content(
            model=model, contents=contents, config=config
        )

        usage = response.usage_metadata
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0

        parsed = getattr(response, "parsed", None)
        if parsed is None:
            # Most often a safety block despite the settings above, or a truncated
            # response. Say which, because the two need different fixes.
            raise LLMError(
                f"returned no parseable output (finish_reason={_finish_reason(response)}). "
                "A safety block here means the filters are still active.",
                prompt_tokens=prompt_tokens,
                output_tokens=output_tokens,
            )
        return parsed, prompt_tokens, output_tokens


class _OpenAIBackend:
    """Any endpoint speaking /v1/chat/completions.

    Structured output is attempted with a strict json_schema first, since that is the
    only mode the server actually enforces. Gateways that reject it fall back to
    json_object with the schema in the system prompt — weaker, so a malformed reply is
    reported as such rather than quietly coerced.
    """

    name = "openai"

    def __init__(self, api_key: str, base_url: str | None):
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._strict_unsupported: set[str] = set()

    @staticmethod
    def _strict_schema(schema: type[BaseModel]) -> dict:
        """Pydantic's JSON schema, tightened to what strict mode requires.

        Strict mode demands additionalProperties:false on every object and every
        property listed in required — Pydantic emits neither for optional fields.
        """
        root = schema.model_json_schema()

        def tighten(node: dict) -> None:
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}).keys())
            for key in ("properties", "$defs", "definitions"):
                for child in node.get(key, {}).values():
                    if isinstance(child, dict):
                        tighten(child)
            for key in ("items", "additionalItems"):
                if isinstance(node.get(key), dict):
                    tighten(node[key])
            for key in ("anyOf", "oneOf", "allOf"):
                for child in node.get(key, []):
                    if isinstance(child, dict):
                        tighten(child)

        tighten(root)
        return root

    def _messages(
        self,
        *,
        prompt: str,
        system_instruction: str | None,
        images: list[bytes] | None,
        schema_hint: dict | None,
    ) -> list[dict]:
        system = system_instruction or ""
        if schema_hint is not None:
            system = (
                f"{system}\n\nReply with JSON matching this schema exactly:\n"
                f"{json.dumps(schema_hint)}"
            ).strip()

        content: list[dict] = [{"type": "text", "text": prompt}]
        for image in images or []:
            b64 = base64.b64encode(image).decode("ascii")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
            )

        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": content})
        return messages

    async def complete(
        self,
        *,
        model: str,
        prompt: str,
        schema: type[T],
        system_instruction: str | None,
        images: list[bytes] | None,
    ) -> tuple[T, int, int]:
        strict = model not in self._strict_unsupported
        kwargs: dict = {
            "model": model,
            "temperature": 0,
            "seed": DETERMINISTIC_SEED,
        }

        if strict:
            kwargs["messages"] = self._messages(
                prompt=prompt,
                system_instruction=system_instruction,
                images=images,
                schema_hint=None,
            )
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": self._strict_schema(schema),
                    "strict": True,
                },
            }
            try:
                response = await self._client.chat.completions.create(**kwargs)
            except Exception as exc:
                if not _looks_like_unsupported_format(exc):
                    raise
                # Remember, so the next call for this model does not pay the round trip.
                log.info("strict json_schema unsupported; falling back", model=model)
                self._strict_unsupported.add(model)
                strict = False

        if not strict:
            kwargs["messages"] = self._messages(
                prompt=prompt,
                system_instruction=system_instruction,
                images=images,
                schema_hint=schema.model_json_schema(),
            )
            kwargs["response_format"] = {"type": "json_object"}
            response = await self._client.chat.completions.create(**kwargs)

        usage = response.usage
        spent = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
        }

        choice = response.choices[0]
        if getattr(choice.message, "refusal", None):
            # OpenAI-compatible endpoints have no BLOCK_NONE. A refusal on this corpus
            # is expected occasionally; surfacing it beats recording a false negative.
            raise LLMError(f"model refused: {choice.message.refusal}", **spent)

        text = choice.message.content
        if not text:
            raise LLMError(f"empty response (finish_reason={choice.finish_reason})", **spent)

        try:
            parsed = schema.model_validate_json(text)
        except Exception as exc:
            raise LLMError(
                f"response did not match {schema.__name__}: {exc}", **spent
            ) from exc

        return parsed, spent["prompt_tokens"], spent["output_tokens"]


def _looks_like_unsupported_format(exc: Exception) -> bool:
    """Whether the endpoint rejected json_schema rather than the request being wrong."""
    text = str(exc).lower()
    return "response_format" in text or "json_schema" in text


class LLMClient:
    """Structured, deterministic classification over the configured provider."""

    def __init__(self, session: AsyncSession, api_key: str | None = None):
        self.session = session
        self.settings = get_settings()
        self._backend = _build_backend(self.settings, api_key)

    @property
    def provider(self) -> str:
        return self._backend.name

    async def generate(
        self,
        *,
        model: str,
        prompt: str,
        schema: type[T],
        purpose: str,
        prompt_version: str,
        system_instruction: str | None = None,
        case_id: str | None = None,
        post_id: str | None = None,
        images: list[bytes] | None = None,
    ) -> T:
        """Call the model and return a parsed schema instance.

        Raises BudgetExceededError before spending anything, and LLMError on failure.
        Every call is written to the ledger — including failures, which still cost.
        """
        await check_budget(self.session, case_id=case_id)

        started = time.monotonic()
        try:
            parsed, prompt_tokens, output_tokens = await self._backend.complete(
                model=model,
                prompt=prompt,
                schema=schema,
                system_instruction=system_instruction,
                images=images,
            )
        except Exception as exc:  # SDKs raise a wide range of transport/API errors
            # A refused or unparseable response still burned its prompt tokens, so the
            # ledger takes whatever the backend managed to read off the response.
            await record_call(
                self.session,
                model=model,
                purpose=purpose,
                prompt_version=prompt_version,
                prompt_tokens=getattr(exc, "prompt_tokens", 0),
                output_tokens=getattr(exc, "output_tokens", 0),
                latency_ms=int((time.monotonic() - started) * 1000),
                case_id=case_id,
                post_id=post_id,
                succeeded=False,
                error=str(exc)[:2000],
            )
            if isinstance(exc, LLMError):
                raise LLMError(f"{purpose} on {model}: {exc}") from exc
            raise LLMError(f"{purpose} call to {model} failed: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        await record_call(
            self.session,
            model=model,
            purpose=purpose,
            prompt_version=prompt_version,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            case_id=case_id,
            post_id=post_id,
        )

        log.debug(
            "LLM call",
            purpose=purpose,
            model=model,
            provider=self._backend.name,
            latency_ms=latency_ms,
        )
        return parsed


def _build_backend(settings, api_key: str | None):
    """Pick the backend for the configured provider, failing with the fix to apply."""
    if settings.llm_provider == "openai":
        key = api_key or settings.openai_api_key
        if not key:
            raise LLMError(
                "LLM_PROVIDER=openai but no key configured — set OPENAI_API_KEY "
                "(`ankedo configure set OPENAI_API_KEY=...`)"
            )
        return _OpenAIBackend(key, settings.openai_base_url)

    key = api_key or settings.gemini_api_key
    if not key:
        raise LLMError(
            "no Gemini API key configured — set GEMINI_API_KEY (run `ankedo setup`)"
        )
    return _GeminiBackend(key)


def _finish_reason(response) -> str:
    try:
        return str(response.candidates[0].finish_reason)
    except (AttributeError, IndexError):
        return "unknown"

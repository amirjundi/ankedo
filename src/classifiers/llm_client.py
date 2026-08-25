"""Gemini client for the classification committee.

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
"""
from __future__ import annotations

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
    """Model call failed, or returned something unparseable."""


class LLMClient:
    """Thin wrapper over google-genai for structured, deterministic classification."""

    def __init__(self, session: AsyncSession, api_key: str | None = None):
        self.session = session
        self.settings = get_settings()
        key = api_key or self.settings.gemini_api_key
        if not key:
            raise LLMError(
                "no Gemini API key configured — set GEMINI_API_KEY (run `ankedo setup`)"
            )
        self._client = genai.Client(api_key=key)

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

        started = time.monotonic()
        try:
            response = await self._client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
        except Exception as exc:  # SDK raises a wide range of transport/API errors
            await record_call(
                self.session,
                model=model,
                purpose=purpose,
                prompt_version=prompt_version,
                latency_ms=int((time.monotonic() - started) * 1000),
                case_id=case_id,
                post_id=post_id,
                succeeded=False,
                error=str(exc)[:2000],
            )
            raise LLMError(f"{purpose} call to {model} failed: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        usage = response.usage_metadata
        await record_call(
            self.session,
            model=model,
            purpose=purpose,
            prompt_version=prompt_version,
            prompt_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            latency_ms=latency_ms,
            case_id=case_id,
            post_id=post_id,
        )

        parsed = getattr(response, "parsed", None)
        if parsed is None:
            # Most often a safety block despite the settings above, or a truncated
            # response. Say which, because the two need different fixes.
            raise LLMError(
                f"{purpose} returned no parseable output "
                f"(finish_reason={_finish_reason(response)}). "
                "A safety block here means the filters are still active."
            )

        log.debug("LLM call", purpose=purpose, model=model, latency_ms=latency_ms)
        return parsed


def _finish_reason(response) -> str:
    try:
        return str(response.candidates[0].finish_reason)
    except (AttributeError, IndexError):
        return "unknown"

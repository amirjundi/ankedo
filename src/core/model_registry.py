"""Model metadata from models.dev.

A `/v1/models` listing tells you which models an endpoint serves. It does not tell
you what any of them are — a bare OpenAI-shaped listing is ids and nothing else. So
picking a model for a role meant inferring from the name, which was wrong twice:
"3b" was not recognised as small, and a "-v" hint matched deepseek-v3, which cannot
see.

models.dev is a community registry of several thousand models across a hundred-odd
providers, carrying modality, cost, context window and — the field that matters most
here — whether a model supports structured output. The whole classification committee
is built on JSON-schema responses, so a model without it does not merely perform
badly, it cannot be used at all.

The two sources answer different questions and are joined by id: the endpoint says
what it serves, the registry says what those things are.

Borrowed from NousResearch/hermes-agent, which uses the same registry. Its caching
discipline is worth copying too, and is why this module looks the way it does:

- Serve a stale cache rather than block on the network. A slow registry must never
  be the reason setup hangs.
- Never fetch on a hot path. Only setup and `configure list-models` pass
  allow_network=True; everything else reads cache or gets nothing.
- Back off after a failure instead of retrying per call.
- Reject a corrupt cache rather than serving {} and silently losing every lookup.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger()

REGISTRY_URL = "https://models.dev/api.json"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_PATH = PROJECT_ROOT / "data" / "models_dev.json"

# The registry changes daily at most, and a stale entry costs nothing here — it only
# informs a default the operator can override.
CACHE_TTL = 24 * 3600
# After a failure, stop trying for a while rather than pausing every lookup.
RETRY_AFTER = 300

_cache: dict | None = None
_cache_time: float = 0.0
_retry_after: float = 0.0


@dataclass(frozen=True)
class ModelFacts:
    """What the registry knows about one model."""

    id: str
    name: str = ""
    vision: bool | None = None
    structured_output: bool | None = None
    tool_call: bool | None = None
    cost_in: float | None = None
    cost_out: float | None = None
    context: int | None = None

    @property
    def usable_for_classification(self) -> bool:
        """The committee parses every response into a Pydantic schema."""
        return self.structured_output is not False


def _load_disk() -> dict | None:
    try:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("models.dev cache unreadable — ignoring", error=str(exc))
        return None
    # A cache that parsed but is not a populated mapping would silently answer every
    # lookup with "unknown", which looks identical to a model the registry lacks.
    if not isinstance(raw, dict) or not raw.get("providers"):
        log.warning("models.dev cache is empty or malformed — ignoring")
        return None
    return raw


def _save_disk(providers: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"fetched_at": time.time(), "providers": providers}),
            encoding="utf-8",
        )
        tmp.replace(CACHE_PATH)
    except Exception as exc:  # a cache we cannot write is not a failure to report
        log.debug("Could not write models.dev cache", error=str(exc))


def _fetch() -> dict | None:
    try:
        import httpx

        resp = httpx.get(REGISTRY_URL, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict) or not payload:
            return None
        return payload
    except Exception as exc:
        log.info("models.dev unavailable", error=str(exc).splitlines()[0][:120])
        return None


def _registry(allow_network: bool) -> dict:
    """The provider→models mapping, from memory, disk, or the network in that order."""
    global _cache, _cache_time, _retry_after

    now = time.time()
    if _cache is not None and now - _cache_time < CACHE_TTL:
        return _cache

    disk = _load_disk()
    if disk is not None:
        _cache = disk["providers"]
        _cache_time = disk.get("fetched_at", 0)
        # Fresh enough, or we are on a hot path: serve it either way.
        if now - _cache_time < CACHE_TTL or not allow_network:
            return _cache

    if not allow_network or now < _retry_after:
        return _cache or {}

    fetched = _fetch()
    if fetched is None:
        _retry_after = now + RETRY_AFTER
        return _cache or {}

    _cache = fetched
    _cache_time = now
    _save_disk(fetched)
    return _cache


def _to_facts(model_id: str, raw: dict) -> ModelFacts:
    modalities = (raw.get("modalities") or {}).get("input") or []
    cost = raw.get("cost") or {}
    limit = raw.get("limit") or {}
    return ModelFacts(
        id=model_id,
        name=str(raw.get("name") or ""),
        vision="image" in [str(m).lower() for m in modalities] if modalities else None,
        structured_output=raw.get("structured_output"),
        tool_call=raw.get("tool_call"),
        cost_in=cost.get("input"),
        cost_out=cost.get("output"),
        context=(limit.get("context") if isinstance(limit.get("context"), int) else None),
    )


def lookup(model_id: str, *, allow_network: bool = False) -> ModelFacts | None:
    """Facts about one model, searched across every provider in the registry.

    Matched on the bare id as well as the full one, because the same model is listed
    as "llama-3.3-70b-instruct" by one gateway and
    "meta-llama/llama-3.3-70b-instruct" by another.
    """
    registry = _registry(allow_network)
    if not registry:
        return None

    wanted = model_id.strip().lower()
    bare = wanted.rsplit("/", 1)[-1]

    for provider in registry.values():
        models = (provider or {}).get("models") or {}
        for candidate_id, raw in models.items():
            low = str(candidate_id).lower()
            if low == wanted or low.rsplit("/", 1)[-1] == bare:
                return _to_facts(str(candidate_id), raw)
    return None


def annotate(model_ids: list[str], *, allow_network: bool = False) -> dict[str, ModelFacts]:
    """Facts for each id the registry recognises. Unknown ids are simply absent."""
    registry = _registry(allow_network)
    if not registry:
        return {}

    # One pass over the registry rather than one per id: it holds several thousand
    # models and the caller may be asking about hundreds.
    wanted = {mid.strip().lower(): mid for mid in model_ids}
    bare = {mid.strip().lower().rsplit("/", 1)[-1]: mid for mid in model_ids}

    found: dict[str, ModelFacts] = {}
    for provider in registry.values():
        for candidate_id, raw in ((provider or {}).get("models") or {}).items():
            low = str(candidate_id).lower()
            original = wanted.get(low) or bare.get(low.rsplit("/", 1)[-1])
            if original and original not in found:
                found[original] = _to_facts(original, raw)
    return found


def refresh(*, force: bool = False) -> int:
    """Pull the registry now. Returns how many providers it holds."""
    global _cache_time, _retry_after
    if force:
        _cache_time = 0.0
        _retry_after = 0.0
    return len(_registry(allow_network=True))

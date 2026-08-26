"""
Interactive setup wizard for AnkEdo — Hermes/OpenClaw-style first-run configuration.

Usage:
    ankedo setup              # Interactive wizard
    ankedo setup --reconfigure # Re-run on existing config
    ankedo setup --non-interactive  # Headless (all config from env/flags)
"""
from __future__ import annotations

import os
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
ENV_EXAMPLE = PROJECT_ROOT / ".env.example"

# ── AI Provider Definitions ──────────────────────────────────────────────────

# A menu entry is a promise the runtime has to keep. Both of these have a backend in
# src/classifiers/llm_client.py; nothing else belongs here until it does. (The wizard
# used to offer Anthropic and a "custom" endpoint with no adapter behind either —
# choosing one wrote a config whose first classification died.)
PROVIDERS = {
    "gemini": {
        "name": "Google Gemini",
        "key_env": "GEMINI_API_KEY",
        "key_prefix": "AIza",
        "key_url": "https://aistudio.google.com/apikey",
        "asks_base_url": False,
        # Keep in step with the defaults in src/core/settings.py.
        "models": {
            "triage": "gemini-3.5-flash-lite",
            "specialist": "gemini-3.6-flash",
            "critic": "gemini-3.5-flash-lite",
            "target_group": "gemini-3.5-flash-lite",
            "vision": "gemini-3.6-flash",
            "chat": "gemini-3.6-flash",
        },
    },
    "openai": {
        "name": "OpenAI-compatible",
        "key_env": "OPENAI_API_KEY",
        "key_prefix": "sk-",
        "key_url": "https://platform.openai.com/api-keys",
        # Same wire format serves OpenRouter, Groq, Together, DeepSeek, Ollama and
        # LM Studio — the base URL is what picks between them.
        "asks_base_url": True,
        "models": {
            "triage": "gpt-4o-mini",
            "specialist": "gpt-4o",
            "critic": "gpt-4o-mini",
            "target_group": "gpt-4o-mini",
            "vision": "gpt-4o",
            "chat": "gpt-4o-mini",
        },
    },
}

@dataclass(frozen=True)
class ProviderChoice:
    """One row in the provider menu.

    The operator picks a service, not an implementation. Which backend serves it —
    google-genai or the OpenAI-compatible one — and what base URL that implies are
    ours to work out; asking someone to choose "OpenAI-compatible" and then pick
    OpenRouter from a second menu makes them model our code to answer a question
    about their account.
    """

    label: str
    backend: str
    base_url: str = ""
    note: str = ""
    prompts_for_url: bool = False


# The list is a shortcut, not a limit: the last row reaches anything speaking
# /v1/chat/completions, so a new service never requires editing this file.
PROVIDER_CHOICES = [
    ProviderChoice("Google Gemini", "gemini", note="recommended — see below"),
    ProviderChoice("OpenAI", "openai"),
    ProviderChoice("OpenRouter", "openai", "https://openrouter.ai/api/v1",
                   "many models, incl. free tiers"),
    ProviderChoice("Groq", "openai", "https://api.groq.com/openai/v1", "fast, free tier"),
    ProviderChoice("Together", "openai", "https://api.together.xyz/v1"),
    ProviderChoice("DeepSeek", "openai", "https://api.deepseek.com/v1"),
    ProviderChoice("Ollama", "openai", "http://localhost:11434/v1", "local, no key needed"),
    ProviderChoice("LM Studio", "openai", "http://localhost:1234/v1", "local, no key needed"),
    ProviderChoice("Other / custom", "openai", note="any OpenAI-compatible URL",
                   prompts_for_url=True),
]

# A local runtime or a free proxy often wants no credential, but the OpenAI SDK
# refuses to construct without one. Send a placeholder rather than making the
# operator invent a fake key or leaving setup in a state that cannot start.
NO_KEY_PLACEHOLDER = "not-needed"

# .env keys for each model role, in the order the wizard shows them.
MODEL_ENV_KEYS = {
    "triage": ("TRIAGE_MODEL", "Triage — first-pass filter"),
    "specialist": ("SPECIALIST_MODEL", "Specialist — deep Arabic/Kurdish analysis"),
    "critic": ("CRITIC_MODEL", "Critic — anti-hallucination review"),
    "target_group": ("TARGET_GROUP_MODEL", "Target group — who the speech targets"),
    "vision": ("VISION_MODEL", "Vision — image and video analysis"),
    "chat": ("CHAT_AGENT_MODEL", "Chat — conversational admin interface"),
}


def _banner():
    """Print the welcome banner."""
    console.print()
    console.print(
        Panel(
            Text.from_markup(
                "[bold cyan]🔺 AnkEdo — First-Run Setup[/]\n"
                "[dim]AI-Powered Hate Speech Monitoring Agent[/]\n"
                "[dim]for Arabic & Kurdish Social Media Discourse[/]"
            ),
            box=box.DOUBLE,
            border_style="cyan",
            padding=(1, 4),
        )
    )
    console.print()


def _step_header(step: int, total: int, title: str):
    console.print()
    console.rule(f"[bold]Step {step}/{total} — {title}[/]", style="cyan")
    console.print()


def _validate_api_key(provider_id: str, api_key: str, base_url: str | None = None) -> bool:
    """Test the API key with a minimal request."""
    try:
        import httpx
    except ImportError:
        console.print("[yellow]⚠ httpx not installed — skipping key validation[/]")
        return True

    # Listing models is the cheapest authenticated call — no tokens spent, and it
    # fails on a revoked key exactly as a generate call would.
    try:
        if provider_id == "gemini":
            resp = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key},
                timeout=10,
            )
        else:
            root = (base_url or "https://api.openai.com/v1").rstrip("/")
            resp = httpx.get(
                f"{root}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
    except Exception as e:
        console.print(f"[yellow]⚠ Validation request failed: {e}[/]")
        return False
    return resp.status_code == 200


# An OpenAI-style /models listing mixes in models that cannot answer a prompt. The
# Gemini listing says so explicitly via supportedGenerationMethods; the OpenAI shape
# does not, so they are excluded by name. "text-embedding-3-small" being offered as a
# triage model is the failure this prevents — it sorts first as the cheapest.
_NOT_CHAT = (
    "embedding", "embed", "whisper", "tts", "dall-e", "dalle", "moderation",
    "rerank", "stable-diffusion", "clip", "audio", "transcribe", "speech",
)


def _can_chat(model_id: str) -> bool:
    return not any(word in model_id.lower() for word in _NOT_CHAT)


@dataclass(frozen=True)
class ModelInfo:
    """One model, with whatever the endpoint was willing to say about it.

    Everything past `id` is optional because a plain OpenAI-shaped /v1/models returns
    ids and nothing else. Richer endpoints — OpenRouter, LiteLLM, Copilot — declare
    modality and price, and a declared fact beats anything inferred from the name.
    """

    id: str
    name: str = ""
    vision: bool | None = None
    cost_in: float | None = None
    context: int | None = None
    # The committee parses every response into a Pydantic schema, so a model
    # without structured output cannot do the job at all.
    structured: bool | None = None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.id})" if self.name and self.name != self.id else self.id


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_openai_model(row: dict) -> ModelInfo | None:
    """Read one row of an OpenAI-shaped /models listing.

    The base shape carries only `id`, but the field names below are what the common
    gateways actually add, so reading them costs nothing and removes a guess:
      - `object` distinguishes a model from an embedding or a router
      - OpenRouter: architecture.input_modalities, pricing.prompt
      - Copilot-style: capabilities.supports.vision, capabilities.limits
    """
    model_id = str(row.get("id") or "").strip()
    if not model_id or not _can_chat(model_id):
        return None
    if row.get("object") and row["object"] != "model":
        return None

    capabilities = row.get("capabilities") or {}
    if capabilities.get("type") and capabilities["type"] != "chat":
        return None

    architecture = row.get("architecture") or {}
    modalities = architecture.get("input_modalities") or architecture.get("modality") or []
    if isinstance(modalities, str):
        modalities = [modalities]

    vision = None
    if modalities:
        vision = any("image" in str(m).lower() for m in modalities)
    elif isinstance((capabilities.get("supports") or {}).get("vision"), bool):
        vision = capabilities["supports"]["vision"]

    pricing = row.get("pricing") or {}
    cost_in = _as_float(pricing.get("prompt") or pricing.get("input"))
    if cost_in is None:
        cost_in = _as_float((row.get("cost") or {}).get("input"))

    limits = capabilities.get("limits") or {}
    context = (
        row.get("context_length")
        or row.get("context_window")
        or limits.get("max_context_window_tokens")
    )

    return ModelInfo(
        id=model_id,
        name=str(row.get("name") or "").strip(),
        vision=vision,
        cost_in=cost_in,
        context=int(context) if isinstance(context, (int, float)) else None,
    )


def _enrich(catalog: list[ModelInfo]) -> list[ModelInfo]:
    """Fill gaps from models.dev, without overwriting what the endpoint declared.

    A bare /v1/models gives ids only, which is most local proxies. The registry knows
    what those ids are, so the join turns a list of names into something a default can
    be chosen from. The endpoint still wins where the two disagree: it is serving the
    model, the registry is describing it.
    """
    if not catalog:
        return catalog

    try:
        from src.core.model_registry import annotate
    except Exception:
        return catalog

    facts = annotate([m.id for m in catalog], allow_network=True)
    if not facts:
        return catalog

    out = []
    for model in catalog:
        known = facts.get(model.id)
        if known is None:
            out.append(model)
            continue
        out.append(ModelInfo(
            id=model.id,
            name=model.name or known.name,
            vision=model.vision if model.vision is not None else known.vision,
            cost_in=model.cost_in if model.cost_in is not None else known.cost_in,
            context=model.context or known.context,
            structured=known.structured_output,
        ))
    return out


def _client_url(base_url: str | None) -> str:
    """A base URL a client can actually connect to.

    0.0.0.0 means "every interface" to a server and is not a destination — a proxy
    started with --host 0.0.0.0 reports itself that way, and the operator pastes it
    in. Some stacks route it to loopback, others refuse outright, so it is normalised
    rather than left to chance. The doctor showed exactly this: OPENAI_BASE_URL set to
    http://0.0.0.0:6446/v1.
    """
    root = (base_url or "https://api.openai.com/v1").strip().rstrip("/")
    return root.replace("://0.0.0.0:", "://127.0.0.1:").replace("://[::]:", "://127.0.0.1:")


def _get_models_json(url: str, api_key: str) -> tuple[object | None, str]:
    """GET a /models listing. Returns (payload, problem-description).

    Retries without the Authorization header on a 401/403, because a local proxy
    serving free models often has no auth and rejects a bearer it did not expect —
    and we send "not-needed" as a placeholder for exactly those endpoints.
    """
    import httpx

    attempts = [{"Authorization": f"Bearer {api_key}"} if api_key else {}, {}]
    last = ""
    for headers in attempts:
        try:
            resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        except Exception as exc:
            return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:100]}"

        if resp.status_code in (401, 403) and headers:
            last = f"HTTP {resp.status_code} with a bearer token; retrying without one"
            continue
        if resp.status_code != 200:
            body = " ".join((resp.text or "").split())[:160]
            return None, f"HTTP {resp.status_code} from {url}{' - ' + body if body else ''}"
        try:
            return resp.json(), ""
        except Exception:
            return None, f"{url} returned {resp.headers.get('content-type', 'no content-type')}, not JSON"
    return None, last or "no response"


def fetch_catalog(provider_id: str, api_key: str, base_url: str | None = None) -> list[ModelInfo]:
    """Like fetch_models, but keeps whatever metadata the endpoint declared."""
    try:
        import httpx
    except ImportError:
        return []

    try:
        if provider_id == "gemini":
            resp = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            out = []
            for entry in resp.json().get("models", []):
                if "generateContent" not in (entry.get("supportedGenerationMethods") or []):
                    continue
                out.append(ModelInfo(
                    id=entry["name"].removeprefix("models/"),
                    name=str(entry.get("displayName") or "").strip(),
                    context=entry.get("inputTokenLimit"),
                ))
            return sorted(out, key=lambda m: m.id.lower())

        url = f"{_client_url(base_url)}/models"
        payload, problem = _get_models_json(url, api_key)
        if payload is None:
            console.print(f"[yellow]  {problem}[/]")
            return []

        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            console.print(f"[yellow]  {url} returned {type(rows).__name__}, not a list of models[/]")
            return []

        parsed = [_parse_openai_model(row) for row in rows if isinstance(row, dict)]
        catalog = _enrich(sorted((m for m in parsed if m), key=lambda m: m.id.lower()))
        if rows and not catalog:
            # Something came back and nothing survived: say so, rather than showing an
            # empty list that looks identical to an endpoint serving no models.
            console.print(
                f"[yellow]  {url} listed {len(rows)} entries, none usable as chat models[/]"
            )
        return catalog
    except Exception as exc:
        hint = str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__
        console.print(f"[dim]Could not list models: {hint}[/]")
        return []


def fetch_models(provider_id: str, api_key: str, base_url: str | None = None) -> list[str]:
    """Ask the provider which models it actually serves.

    The per-provider defaults in PROVIDERS are a starting point, not a truth: an
    OpenAI-compatible proxy serves whatever it was configured with — Llama, Qwen,
    DeepSeek, a local GGUF — and "gpt-4o" is simply not one of them. Offering a fixed
    list there produces a config whose every call 404s.

    Returns [] rather than raising when the endpoint cannot be reached or does not
    implement the listing. Some gateways do not, and that is a reason to fall back to
    typing a name, not a reason to stop setup.
    """
    try:
        import httpx
    except ImportError:
        return []

    try:
        if provider_id == "gemini":
            resp = httpx.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key},
                timeout=15,
            )
            resp.raise_for_status()
            return sorted(
                entry["name"].removeprefix("models/")
                for entry in resp.json().get("models", [])
                # Embedding and token-counting models cannot answer a prompt, and
                # listing them invites picking one that fails on the first call.
                if "generateContent" in (entry.get("supportedGenerationMethods") or [])
            )

        root = (base_url or "https://api.openai.com/v1").rstrip("/")
        resp = httpx.get(
            f"{root}/models", headers={"Authorization": f"Bearer {api_key}"}, timeout=15
        )
        resp.raise_for_status()
        payload = resp.json()
        # The OpenAI shape is {"data":[{"id":...}]}; some proxies return a bare list.
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        parsed = [_parse_openai_model(row) for row in rows if isinstance(row, dict)]
        return sorted((m.id for m in parsed if m), key=str.lower)
    except Exception as exc:
        log_hint = str(exc).splitlines()[0][:120] if str(exc) else type(exc).__name__
        console.print(f"[dim]Could not list models: {log_hint}[/]")
        return []


# Words that mark a small, cheap variant when no parameter count is in the name.
_SMALL_WORDS = ("lite", "mini", "flash", "small", "haiku", "nano", "tiny", "instant")
# Substrings that suggest a model can read an image. Deliberately specific: a loose
# pattern like "-v" matches deepseek-v3, which cannot see anything.
_VISION_WORDS = ("vision", "vl", "pixtral", "llava", "4o", "gemini", "claude-3", "sonnet")


def _size_of(name: str) -> float | None:
    """Parameter count in billions, when the name states one — 70b, 3b, 1.5b."""
    match = re.search(r"(\d+(?:\.\d+)?)\s*b(?![a-z0-9])", name.lower())
    return float(match.group(1)) if match else None


def _rank(name: str) -> tuple[int, float]:
    """Sort key from cheapest to most capable.

    Parameter count first where it is stated, since it is the one honest signal a
    model name carries. Otherwise fall back to the marketing words, which at least
    order a vendor's own lineup correctly.
    """
    size = _size_of(name)
    if size is not None:
        return (0, size)
    # More small-words means smaller: "flash-lite" is cheaper than "flash", and
    # without this the tie is broken by listing order, which means nothing.
    hits = sum(word in name.lower() for word in _SMALL_WORDS)
    return (1, -float(hits))


def _suggest_from_catalog(role: str, catalog: list[ModelInfo], fallback: str) -> str:
    """Choose using what the endpoint declared, falling back to the name heuristic.

    A declared price or modality is a fact; "3b" in a model name is a guess that has
    already been wrong twice. Endpoints that declare nothing still get the heuristic,
    because that is all a bare /v1/models supports.
    """
    if not catalog:
        return fallback
    ids = [m.id for m in catalog]
    if fallback in ids:
        return fallback

    if role == "vision":
        declared = [m for m in catalog if m.vision is True]
        if declared:
            # Cheapest that can actually see, where price is known.
            priced = [m for m in declared if m.cost_in is not None]
            return min(priced, key=lambda m: m.cost_in).id if priced else declared[0].id
        if any(m.vision is False for m in catalog) and all(
            m.vision is not None for m in catalog
        ):
            # Every model declared itself text-only: say so rather than picking one
            # that will reject an image.
            return _suggest(role, ids, fallback)

    priced = [m for m in catalog if m.cost_in is not None]
    if priced and role != "vision":
        priced.sort(key=lambda m: m.cost_in)
        return priced[0].id if role in ("triage", "critic", "target_group") else priced[-1].id

    return _suggest(role, ids, fallback)


def _suggest(role: str, available: list[str], fallback: str) -> str:
    """Pick a sensible default for a role out of what the provider actually serves.

    Triage, the critic and group resolution run on every item, so they want the
    cheapest model available; the specialist runs only on what survives triage and
    wants the most capable. Getting this wrong is expensive rather than broken, and
    the operator can override every choice.
    """
    if not available:
        return fallback
    if fallback in available:
        return fallback

    by_cost = sorted(available, key=_rank)

    if role == "vision":
        seeing = [m for m in by_cost if any(w in m.lower() for w in _VISION_WORDS)]
        # No obvious vision model: the largest is the likeliest to be multimodal, and
        # a wrong guess surfaces as a clear API error rather than a silent miss.
        return (seeing or by_cost)[-1]
    if role in ("triage", "critic", "target_group"):
        return by_cost[0]
    return by_cost[-1]


def _warn_unstructured(config: dict, catalog: list[ModelInfo]) -> None:
    """Flag any assigned model the registry says cannot return structured output.

    Every committee response is parsed into a Pydantic schema, so a model without it
    does not degrade — it fails on the first classification, with an error naming a
    schema mismatch rather than the model choice that caused it.
    """
    unstructured = {m.id for m in catalog if m.structured is False}
    hit = [
        (env_key, config[env_key])
        for env_key, _ in MODEL_ENV_KEYS.values()
        if config.get(env_key) in unstructured
    ]
    if not hit:
        return

    console.print()
    console.print(
        "[yellow]⚠ These cannot return structured output, which the classifier requires:[/]"
    )
    for env_key, model in hit:
        console.print(f"    [yellow]{env_key} = {model}[/]")
    console.print("[dim]  They will fail on the first classification. Pick others where you can.[/]")


def _choose_models(config: dict, provider_id: str, provider: dict, api_key: str,
                   base_url: str | None) -> None:
    """Assign a model to each role, from the provider's own list where possible."""
    console.print("[dim]Asking the provider which models it serves...[/]", end=" ")
    catalog = fetch_catalog(provider_id, api_key, base_url)
    available = [m.id for m in catalog]

    if not available:
        console.print("[yellow]⚠ no list available[/]")
        console.print(
            "[dim]This endpoint does not list its models, so the defaults below are a\n"
            "guess. Check them against your provider.[/]\n"
        )
        for role, (env_key, _) in MODEL_ENV_KEYS.items():
            config[env_key] = provider["models"][role]
        console.print(_model_table(config))
        console.print()
        if Confirm.ask("Type the model names yourself?", default=True):
            for role, (env_key, label) in MODEL_ENV_KEYS.items():
                config[env_key] = Prompt.ask(f"  {label}", default=config[env_key])
        return

    console.print(f"[green]✓ {len(available)} available[/]\n")
    for index, model in enumerate(catalog, 1):
        marks = []
        if model.vision:
            marks.append("vision")
        if model.cost_in is not None:
            marks.append(f"${model.cost_in:g}/Mtok")
        if model.structured is False:
            marks.append("[red]no structured output[/]")
        suffix = f"  [dim]— {', '.join(marks)}[/]" if marks else ""
        console.print(f"  [cyan]{index:>3}[/] {model.label}{suffix}")
    console.print()

    for role, (env_key, _) in MODEL_ENV_KEYS.items():
        config[env_key] = _suggest_from_catalog(role, catalog, provider["models"][role])

    console.print(_model_table(config))
    _warn_unstructured(config, catalog)
    console.print()

    if not Confirm.ask("Change any of these?", default=False):
        return

    console.print("[dim]Enter a number from the list, or a model name. Blank keeps it.[/]\n")
    for role, (env_key, label) in MODEL_ENV_KEYS.items():
        answer = Prompt.ask(f"  {label}", default=config[env_key]).strip()
        if not answer:
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(available):
            answer = available[int(answer) - 1]
        elif answer not in available:
            # Not refused: a gateway can serve a model it does not advertise, and the
            # operator may know better than its listing.
            console.print(f"    [yellow]⚠ {answer} is not in the list — using it anyway[/]")
        config[env_key] = answer


def _enrol_with_code(base_url: str, agent_id: str) -> str:
    """Redeem a pairing code, retrying a typo without restarting the wizard.

    Three attempts: a code read over a phone line gets mistyped, and forcing a
    restart of the whole wizard for a wrong character is how operators give up and
    ask for the raw key instead — which is the thing this exists to avoid.
    """
    from src.ettok.enrollment import EnrollmentError, redeem

    for attempt in range(3):
        code = Prompt.ask("  Pairing code")
        if not code.strip():
            return ""

        console.print("  [dim]Redeeming...[/]", end=" ")
        try:
            result = redeem(base_url, code, agent_id)
        except EnrollmentError as exc:
            console.print(f"[red]✗ {exc}[/]")
            if attempt < 2 and Confirm.ask("  Try again?", default=True):
                continue
            return ""

        console.print("[green]✓ enrolled[/]")
        if result.get("agent_id") and result["agent_id"] != agent_id:
            console.print(f"  [dim]Platform assigned agent ID: {result['agent_id']}[/]")
        return result["agent_key"]

    return ""


def _default_agent_id() -> str:
    """Hostname-based, so two agents are distinguishable in the platform's logs.

    A shared default means every agent reports as the same X-Agent-Id, and the
    platform cannot tell which machine sent what — which matters when one of them
    starts hitting checkpoints.
    """
    import socket

    host = socket.gethostname().lower().replace(" ", "-")
    return f"ankedo-{host}"[:64] or "ankedo-agent"


def _validate_agent_key(base_url: str, agent_key: str, agent_id: str) -> tuple[bool, str]:
    """Call heartbeat/ so a bad key fails while the operator still has it on screen.

    Distinguishes the three failures that need different fixes: a rejected key, an
    unreachable host, and a key that is valid but lacks the scope.
    """
    try:
        import httpx
    except ImportError:
        return True, "httpx not installed — skipped"

    url = base_url.rstrip("/") + "/heartbeat/"
    try:
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {agent_key}", "X-Agent-Id": agent_id},
            json={"agent_id": agent_id, "status": "setup"},
            timeout=15,
        )
    except Exception as exc:
        return False, f"could not reach {url} ({type(exc).__name__})"

    if resp.status_code == 401:
        return False, "key rejected — unknown or revoked"
    if resp.status_code == 403:
        return False, "key is valid but lacks the hate_speech_scan scope"
    if resp.status_code >= 500:
        return False, f"platform error {resp.status_code} — the key may still be fine"
    if resp.status_code >= 400:
        return False, f"HTTP {resp.status_code}"
    return True, "ok"


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "•" * (len(key) - 8) + key[-4:]


def _parse_value(raw: str) -> str:
    """The value from the right-hand side of a KEY=VALUE line, without its comment.

    `.env.example` documents most keys with a trailing comment:

        TELEGRAM_BOT_TOKEN=                     # From @BotFather
        TRIAGE_MODEL=gemini-3.5-flash-lite      # Fast, cheap

    Taking everything after the `=` made those comments the values. The wizard then
    reported Telegram as configured because "# From @BotFather" is a non-empty string,
    wrote it into .env, and pydantic loaded it as the bot token. The same bug corrupted
    every model id, LOG_LEVEL, MCP_SERVERS and the proxy list.

    An inline comment needs whitespace before the `#`, so a value that legitimately
    contains one — a password, a URL fragment — survives. A quoted value is taken
    whole, which is the escape hatch for a value starting with `#`.
    """
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]

    cut = len(raw)
    for index, char in enumerate(raw):
        if char == "#" and (index == 0 or raw[index - 1] in " \t"):
            cut = index
            break
    return raw[:cut].strip()


def _load_existing_env() -> dict[str, str]:
    """Parse existing .env file into a dict."""
    config = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                config[key.strip()] = _parse_value(value)
    return config


def _write_env(config: dict[str, str]):
    """Write config dict to .env file, preserving comments from .env.example.

    Anything in `config` that .env.example does not mention is appended rather than
    dropped. The template is a curated subset — GEMINI_API_KEY and the ETTOK_* keys
    were collected by the wizard and then silently discarded here, so the agent came
    up unconfigured after a setup that reported success.
    """
    lines: list[str] = []
    written: set[str] = set()

    if ENV_EXAMPLE.exists():
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
            elif "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                template = _parse_value(stripped.split("=", 1)[1])
                # "AIza..." / "sk-..." are illustrations, not values. Copied through,
                # they read as a configured key and fail only on the first API call.
                if template.endswith("..."):
                    template = ""
                value = config.get(key, template)
                lines.append(f"{key}={value}")
                written.add(key)
            else:
                lines.append(line)

    extra = [k for k in config if k not in written]
    if extra:
        lines.append("")
        lines.append("# ── Set by `ankedo setup` ─────────────────────────────────────")
        lines.extend(f"{key}={config[key]}" for key in extra)

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _model_table(config: dict[str, str]) -> Table:
    table = Table(box=box.SIMPLE_HEAVY, show_header=True)
    table.add_column("Role", style="cyan")
    table.add_column(".env key", style="dim")
    table.add_column("Model", style="green")
    for _role, (env_key, label) in MODEL_ENV_KEYS.items():
        table.add_row(label, env_key, config.get(env_key, "[dim]unset[/]"))
    return table


def show_models():
    """Print the current model assignments — `ankedo configure models`."""
    config = _load_existing_env()
    if not config:
        console.print("[red]✗ No .env found. Run 'ankedo setup' first.[/]")
        sys.exit(1)
    console.print()
    console.print(_model_table(config))
    console.print(
        "\n[dim]Change one with:[/]\n"
        "  [cyan]ankedo configure set SPECIALIST_MODEL=gemini-3.6-flash[/]\n"
    )


def list_available_models():
    """Print what the configured provider serves — `ankedo configure list-models`."""
    config = _load_existing_env()
    if not config:
        console.print("[red]✗ No .env found. Run 'ankedo setup' first.[/]")
        sys.exit(1)

    provider_id = (config.get("LLM_PROVIDER") or "gemini").strip().lower()
    if provider_id not in PROVIDERS:
        console.print(f"[red]✗ Unknown LLM_PROVIDER={provider_id!r}[/]")
        sys.exit(1)

    key = config.get(PROVIDERS[provider_id]["key_env"], "")
    if not key:
        console.print(f"[red]✗ {PROVIDERS[provider_id]['key_env']} is not set.[/]")
        sys.exit(1)

    base_url = config.get("OPENAI_BASE_URL") or None
    where = base_url or PROVIDERS[provider_id]["name"]
    console.print(f"\n[dim]Asking {where}...[/]")

    available = fetch_models(provider_id, key, base_url)
    if not available:
        console.print(
            "[yellow]⚠ This endpoint does not list its models.[/]\n"
            "[dim]Set one directly: ankedo configure set SPECIALIST_MODEL=<name>[/]\n"
        )
        return

    in_use = {config.get(env_key) for env_key, _ in MODEL_ENV_KEYS.values()}
    console.print(f"\n[bold]{len(available)} models available[/]\n")
    for name in available:
        mark = "[green] ← in use[/]" if name in in_use else ""
        console.print(f"  {name}{mark}")

    console.print()
    console.print(_model_table(config))
    # A model in .env that the provider no longer serves fails on the next call, and
    # the error names the model rather than saying it was withdrawn.
    stale = sorted(m for m in in_use if m and m not in available)
    if stale:
        console.print(
            f"\n[yellow]⚠ Assigned but not served: {', '.join(stale)}[/]\n"
            "[dim]These will fail when called.[/]"
        )
    console.print(
        "\n[dim]Change one with:[/]\n"
        "  [cyan]ankedo configure set SPECIALIST_MODEL=<name>[/]\n"
    )


def set_env_values(pairs: tuple[str, ...]):
    """Set KEY=VALUE pairs in .env — `ankedo configure set`.

    The scriptable path: onboarding a machine over SSH should not require driving an
    interactive wizard.
    """
    config = _load_existing_env()
    if not config:
        console.print("[red]✗ No .env found. Run 'ankedo setup' first.[/]")
        sys.exit(1)

    known = {env_key for env_key, _ in MODEL_ENV_KEYS.values()}
    for pair in pairs:
        if "=" not in pair:
            console.print(f"[red]✗ Expected KEY=VALUE, got '{pair}'[/]")
            sys.exit(1)
        key, _, value = pair.partition("=")
        key = key.strip().upper()
        if not key:
            console.print(f"[red]✗ Empty key in '{pair}'[/]")
            sys.exit(1)
        old = config.get(key)
        config[key] = value.strip()
        # A typo'd model name is only discovered on the first classification, by
        # which point the run has already burned its collection pass.
        if key.endswith("_MODEL") and key not in known:
            console.print(f"[yellow]⚠ {key} is not a model role AnkEdo reads[/]")
        console.print(f"[green]✓[/] {key}: [dim]{old or 'unset'}[/] → [bold]{value.strip()}[/]")

    _write_env(config)
    console.print(f"\n[green]✓ Saved to {ENV_FILE}[/]")
    console.print("[dim]Restart the agent for changes to take effect.[/]")


def run_setup(non_interactive: bool = False, reconfigure: bool = False):
    """Main setup wizard entry point."""
    _banner()

    # Without a terminal, every Confirm.ask reads EOF and rich re-prompts forever —
    # the "Please enter Y or N" loop that ends in Aborted!. Say what to do instead.
    if not non_interactive and not sys.stdin.isatty():
        console.print("[red]✗ No terminal attached — cannot run the interactive wizard.[/]")
        console.print(
            "\n[dim]Run it from a terminal, or configure headlessly:[/]\n"
            "  [cyan]ankedo setup[/]\n"
            "  [cyan]GEMINI_API_KEY=AIza... ankedo setup --non-interactive[/]\n"
        )
        sys.exit(1)

    existing = _load_existing_env()
    total_steps = 6

    # ── Handle existing config ───────────────────────────────────────────
    if existing and not reconfigure and not non_interactive:
        console.print("[yellow]⚡ Existing configuration detected![/]")
        console.print()
        choice = Prompt.ask(
            "What would you like to do?",
            choices=["update", "reconfigure", "keep"],
            default="update",
        )
        if choice == "keep":
            console.print("[green]✓ Keeping existing configuration.[/]")
            return
        elif choice == "reconfigure":
            existing = {}
            console.print("[dim]Starting fresh configuration...[/]")

    config = dict(existing)

    # ── Non-interactive mode ─────────────────────────────────────────────
    if non_interactive:
        console.print("[cyan]Running in non-interactive mode...[/]")
        # The required key is whichever one the selected provider calls with — the
        # old check demanded OPENAI_API_KEY unconditionally, which no backend used.
        provider_id = (
            os.environ.get("LLM_PROVIDER") or config.get("LLM_PROVIDER") or "gemini"
        ).strip().lower()
        if provider_id not in PROVIDERS:
            console.print(f"[red]✗ LLM_PROVIDER must be one of {', '.join(PROVIDERS)}[/]")
            sys.exit(1)
        config["LLM_PROVIDER"] = provider_id

        key_env = PROVIDERS[provider_id]["key_env"]
        if not os.environ.get(key_env) and not config.get(key_env):
            console.print(f"[red]✗ {key_env} is not set[/]")
            console.print(
                "[dim]Export it before running --non-interactive:[/]\n"
                f"[dim]  export {key_env}=...[/]"
            )
            sys.exit(1)

        for key in os.environ:
            upper = key.upper()
            if upper.startswith(("GEMINI_", "OPENAI_", "ANTHROPIC_", "TELEGRAM_",
                                 "WHATSAPP_", "DATABASE_", "LOG_", "API_", "MCP_",
                                 "SECRET_", "ETTOK_", "LLM_", "TRIAGE_", "SPECIALIST_",
                                 "CRITIC_", "TARGET_GROUP_", "VISION_", "CHAT_AGENT_",
                                 "AUTO_", "PACING_", "SESSION_")):
                config[upper] = os.environ[key]

        # Model defaults for the chosen provider, so a headless install lands on a
        # runnable config instead of inheriting another provider's model ids.
        switched = existing.get("LLM_PROVIDER", provider_id) != provider_id
        for role, (env_key, _) in MODEL_ENV_KEYS.items():
            if switched or not config.get(env_key):
                config[env_key] = PROVIDERS[provider_id]["models"][role]

        if not config.get("SECRET_KEY"):
            config["SECRET_KEY"] = secrets.token_hex(32)

        _write_env(config)
        console.print("[green]✓ Configuration saved from environment variables.[/]")
        return

    # ── Step 1: AI Provider ──────────────────────────────────────────────
    _step_header(1, total_steps, "AI Provider")

    console.print("Which provider should the classification committee use?\n")

    for index, entry in enumerate(PROVIDER_CHOICES, 1):
        note = f"  [dim]{entry.note}[/]" if entry.note else ""
        console.print(f"  [cyan]{index:>2}[/] {entry.label:<20}{note}")

    console.print(
        "\n[dim]This tool has to read hate speech in order to classify it. Gemini is\n"
        "the only backend where the client can switch those filters off, and the only\n"
        "one that honours a fixed seed, so results stay reproducible. Anything else\n"
        "may refuse the worst content.[/]\n"
    )

    pick = Prompt.ask(
        "Select provider",
        choices=[str(i) for i in range(1, len(PROVIDER_CHOICES) + 1)],
        default="1",
    )
    entry = PROVIDER_CHOICES[int(pick) - 1]

    provider_id = entry.backend
    provider = PROVIDERS[provider_id]
    config["LLM_PROVIDER"] = provider_id
    console.print(f"\n[green]✓ Selected: {entry.label}[/]")

    base_url = ""
    if entry.prompts_for_url:
        console.print(
            "\n[dim]Any endpoint serving /v1/chat/completions — a self-hosted proxy,\n"
            "a gateway, or a free-model service. Include the /v1.[/]"
        )
        base_url = Prompt.ask("  Base URL").strip()
    elif entry.base_url:
        base_url = Prompt.ask("  Base URL", default=entry.base_url).strip()
    if base_url:
        config["OPENAI_BASE_URL"] = base_url

    # ── Step 2: API Key ──────────────────────────────────────────────────
    _step_header(2, total_steps, "API Key")

    console.print(f"[dim]Get one at {provider['key_url']}[/]\n")

    existing_key = config.get(provider["key_env"], "")
    if existing_key and not existing_key.endswith("..."):
        console.print(f"[dim]Current key: {_mask_key(existing_key)}[/]")
        if not Confirm.ask("Update this key?", default=False):
            api_key = existing_key
        else:
            api_key = Prompt.ask(f"Enter your {provider['name']} API key")
    elif provider_id == "openai" and config.get("OPENAI_BASE_URL"):
        # A local model or an open proxy has no key to give.
        console.print("[dim]Leave blank if this endpoint does not need a key.[/]")
        api_key = Prompt.ask("Enter the API key", default="").strip()
    else:
        api_key = Prompt.ask(
            f"Enter your {provider['name']} API key (starts with {provider['key_prefix']})"
        )

    if not api_key and config.get("OPENAI_BASE_URL"):
        api_key = NO_KEY_PLACEHOLDER
        console.print(f"[dim]No key given — using '{NO_KEY_PLACEHOLDER}'.[/]")

    # Validate
    console.print("[dim]Validating API key...[/]", end=" ")
    if _validate_api_key(provider_id, api_key, config.get("OPENAI_BASE_URL")):
        console.print("[green]✓ Key is valid![/]")
    else:
        console.print("[yellow]⚠ Could not validate key (network issue or invalid key)[/]")
        if not Confirm.ask("Continue anyway?", default=True):
            sys.exit(1)

    config[provider["key_env"]] = api_key

    # ── Step 3: Model Configuration ──────────────────────────────────────
    _step_header(3, total_steps, "Model Configuration")

    # All six roles, from the provider's own list where it has one. The defaults in
    # PROVIDERS are only a fallback: an OpenAI-compatible proxy serves whatever it was
    # configured with, and gpt-4o is usually not among it.
    _choose_models(config, provider_id, provider, api_key, config.get("OPENAI_BASE_URL"))
    console.print("[green]✓ Models set[/]")

    # ── Step 4: Notification Channels ────────────────────────────────────
    _step_header(4, total_steps, "Notification Channels (Optional)")

    console.print(
        "[dim]The agent can notify you via Telegram or WhatsApp.\n"
        "You can skip this and configure later with: ankedo configure[/]\n"
    )

    # Telegram
    if Confirm.ask("Configure Telegram notifications?", default=False):
        config["TELEGRAM_BOT_TOKEN"] = Prompt.ask("  Bot token (from @BotFather)")
        config["TELEGRAM_ADMIN_CHAT_ID"] = Prompt.ask("  Your admin chat ID")
        console.print("[green]  ✓ Telegram configured[/]")
    else:
        console.print("[dim]  ⏭  Telegram skipped[/]")

    # WhatsApp
    if Confirm.ask("Configure WhatsApp notifications?", default=False):
        config["WHATSAPP_PHONE_NUMBER_ID"] = Prompt.ask("  Phone Number ID (Meta Dashboard)")
        config["WHATSAPP_ACCESS_TOKEN"] = Prompt.ask("  Access Token")
        config["WHATSAPP_ADMIN_PHONE"] = Prompt.ask("  Admin phone (e.g., +9647XXXXXXXX)")
        console.print("[green]  ✓ WhatsApp configured[/]")
    else:
        console.print("[dim]  ⏭  WhatsApp skipped[/]")

    # ── Step 5: Ettok Platform ───────────────────────────────────────────
    _step_header(5, total_steps, "Ettok Platform Connection")

    console.print(
        "The platform owns the lexicon and holds the review queue. The agent pulls\n"
        "terms from it each run and submits what it finds.\n"
    )
    console.print(
        "[dim]Create a key in the Django admin under 'Agent keys' with the\n"
        "hate_speech_scan scope. The plaintext is shown once and cannot be recovered.[/]\n"
    )

    if Confirm.ask("Connect to an Ettok platform now?", default=True):
        base_url = Prompt.ask(
            "  Platform URL",
            default=config.get("ETTOK_BASE_URL") or "https://ettok.net/api/hermes/",
        )
        # Hostname rather than a fixed default, so two agents are distinguishable in
        # the platform's logs without anyone configuring it.
        agent_id = Prompt.ask("  Agent ID", default=_default_agent_id())

        console.print(
            "\n  [bold]How will you authenticate?[/]\n"
            "  [cyan]1[/] Pairing code  [dim]— short code from the admin (recommended)[/]\n"
            "  [cyan]2[/] Agent key     [dim]— paste the long key directly[/]\n"
        )
        console.print(
            "  [dim]A pairing code is safe to send over chat: it is single-use and\n"
            "  expires in minutes. The long-lived key never leaves the platform.[/]\n"
        )
        method = Prompt.ask("  Choose", choices=["1", "2"], default="1")

        agent_key = ""
        if method == "1":
            agent_key = _enrol_with_code(base_url, agent_id)
        else:
            agent_key = Prompt.ask("  Agent key", password=True)

        if agent_key:
            console.print("\n  [dim]Verifying...[/]", end=" ")
            ok, detail = _validate_agent_key(base_url, agent_key, agent_id)
            if ok:
                console.print("[green]✓ connected[/]")
            else:
                # Told now, while the key is still on screen — not on the first scan.
                console.print(f"[red]✗ {detail}[/]")
                if not Confirm.ask("  Save it anyway?", default=False):
                    agent_key = ""

        if agent_key:
            config["ETTOK_BASE_URL"] = base_url
            config["ETTOK_AGENT_KEY"] = agent_key
            config["ETTOK_AGENT_ID"] = agent_id
    else:
        console.print(
            "[dim]  Skipped. The agent runs standalone; re-run `ankedo setup` to\n"
            "  connect later.[/]"
        )

    console.print()

    # ── Step 6: Review & Save ────────────────────────────────────────────
    _step_header(6, total_steps, "Review & Save")

    # Fill in defaults for anything not set
    config.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/ankedo.db")
    config.setdefault("DATA_DIR", "./data")
    config.setdefault("LOOP_INTERVAL_SECONDS", "60")
    config.setdefault("AUTO_FLAG_THRESHOLD", "0.75")
    config.setdefault("BORDERLINE_LOW", "0.50")
    config.setdefault("BORDERLINE_HIGH", "0.74")
    config.setdefault("MAX_REVIEW_BATCH_SIZE", "25")
    config.setdefault("API_HOST", "127.0.0.1")
    config.setdefault("API_PORT", "8000")
    config.setdefault("LOG_LEVEL", "INFO")
    config.setdefault("LOG_DIR", "./logs")
    config.setdefault("EVIDENCE_DIR", "./evidence")
    config.setdefault("SCREENSHOT_FORMAT", "png")

    if not config.get("SECRET_KEY"):
        config["SECRET_KEY"] = secrets.token_hex(32)

    # Summary table
    summary = Table(
        title="Configuration Summary",
        box=box.ROUNDED,
        title_style="bold cyan",
        show_header=True,
        header_style="bold",
    )
    summary.add_column("Setting", style="cyan", min_width=20)
    summary.add_column("Value", style="green")

    summary.add_row("AI Provider", provider["name"])
    summary.add_row("API Key", _mask_key(api_key))
    summary.add_row("Triage Model", config["TRIAGE_MODEL"])
    summary.add_row("Specialist Model", config["SPECIALIST_MODEL"])
    summary.add_row("Critic Model", config["CRITIC_MODEL"])
    summary.add_row("Chat Model", config["CHAT_AGENT_MODEL"])
    summary.add_row("", "")
    summary.add_row(
        "Telegram",
        "[green]Configured ✓[/]" if config.get("TELEGRAM_BOT_TOKEN") else "[dim]Not configured[/]",
    )
    summary.add_row(
        "WhatsApp",
        "[green]Configured ✓[/]" if config.get("WHATSAPP_ACCESS_TOKEN") else "[dim]Not configured[/]",
    )
    summary.add_row("", "")
    summary.add_row(
        "Ettok Platform",
        f"[green]{config['ETTOK_BASE_URL']} ✓[/]"
        if config.get("ETTOK_AGENT_KEY")
        else "[dim]Not connected[/]",
    )
    summary.add_row("", "")
    summary.add_row("Dashboard", f"http://{config['API_HOST']}:{config['API_PORT']}")
    summary.add_row("Database", config["DATABASE_URL"])

    console.print(summary)
    console.print()

    if Confirm.ask("Save this configuration?", default=True):
        _write_env(config)
        console.print(f"\n[green]✓ Configuration saved to {ENV_FILE}[/]")
        console.print()

        # Initialize database
        console.print("[dim]Initializing database...[/]", end=" ")
        try:
            import asyncio
            from src.core.database import init_db
            asyncio.run(init_db())
            console.print("[green]✓ Database ready[/]")
        except Exception as e:
            console.print(f"[yellow]⚠ Database init deferred: {e}[/]")

        # Create data directories
        for d in ["data", "evidence", "logs", "screenshots"]:
            (PROJECT_ROOT / d).mkdir(exist_ok=True)

        console.print()
        console.print(
            Panel(
                Text.from_markup(
                    "[green bold]🚀 Setup complete![/]\n\n"
                    "Next steps:\n"
                    "  [cyan]ankedo doctor[/]   — Verify everything works\n"
                    "  [cyan]ankedo start[/]    — Launch the agent + dashboard\n"
                    "  [cyan]ankedo setup[/]    — Re-run this wizard anytime"
                ),
                border_style="green",
                padding=(1, 2),
            )
        )
    else:
        console.print("[yellow]Setup cancelled. Run 'ankedo setup' to try again.[/]")

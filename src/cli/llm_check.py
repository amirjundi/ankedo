"""`ankedo test-llm` — one real call, and exactly where it breaks.

"The agent doesn't respond in the chat" can mean any of six things, and the chat
surface reports most of them identically. This walks the same path a classification
takes and stops at the first thing that is actually wrong:

  1. Is a provider and key configured at all?
  2. Is the endpoint reachable?
  3. Does it serve the models we are configured to call? — the failure that produced
     the original silence: an OpenAI-compatible proxy configured with gemini-* model
     names left over from a previous provider, so every request 404s.
  4. Will it answer a plain prompt?
  5. Will it answer with *structured output*? The committee parses every response
     into a Pydantic schema, so a model that cannot do this is unusable here however
     well it chats.
  6. Does the full LLMClient path work, budget ledger and all?
"""
from __future__ import annotations

import asyncio

import structlog
from pydantic import BaseModel, Field
from rich.console import Console

from src.core.settings import get_settings

log = structlog.get_logger()
console = Console()


class _Probe(BaseModel):
    """Deliberately trivial: if this cannot be filled, nothing here can work."""

    answer: str = Field(description="the word OK")
    confident: bool = Field(description="always true")


def _line(ok: bool | None, label: str, detail: str = "") -> None:
    mark = {True: "[green]✓[/]", False: "[red]✗[/]", None: "[yellow]⚠[/]"}[ok]
    console.print(f"  {mark} {label}" + (f"  [dim]{detail}[/]" if detail else ""))


async def run_llm_check() -> bool:
    from src.cli.setup_wizard import PROVIDERS, _client_url, fetch_models
    from src.classifiers.llm_client import LLMClient, LLMError
    from src.core.database import get_session, init_db

    settings = get_settings()
    console.print("\n[bold cyan]🔺 AnkEdo — LLM check[/]\n")

    # ── 1. Configuration ────────────────────────────────────────────────────
    provider = settings.llm_provider
    key_env = PROVIDERS[provider]["key_env"]
    key = getattr(settings, key_env.lower(), None)
    base_url = settings.openai_base_url if provider == "openai" else None

    console.print(f"  [dim]provider[/]  {provider}")
    if base_url:
        resolved = _client_url(base_url)
        console.print(f"  [dim]endpoint[/]  {base_url}" +
                      (f"  [yellow]→ {resolved}[/]" if resolved != base_url.rstrip('/') else ""))
    if not key:
        _line(False, f"{key_env} is not set", "run `ankedo setup`")
        return False
    _line(True, f"{key_env} configured")

    configured = {
        role: getattr(settings, f"{role}_model")
        for role in ("triage", "specialist", "critic", "vision", "chat_agent")
    }

    # ── 2/3. Reachable, and does it serve what we ask for? ──────────────────
    console.print("\n[bold]Endpoint[/]")
    available = fetch_models(provider, key, base_url)
    if not available:
        _line(None, "could not list models", "the endpoint may not implement /models")
    else:
        _line(True, f"{len(available)} models served")
        served = set(available)
        missing = {r: m for r, m in configured.items() if m not in served}
        if missing:
            _line(False, "configured models the endpoint does not serve")
            for role, model in missing.items():
                console.print(f"      [red]{role}_model = {model}[/]")
            console.print(
                "\n  [dim]Every call using these returns 404, which the chat reports as\n"
                "  a failure to reach the model. Available here:[/]\n"
            )
            for name in available[:12]:
                console.print(f"      [cyan]{name}[/]")
            console.print(
                "\n  [dim]Fix with:[/]\n"
                f"    [cyan]ankedo configure set CHAT_AGENT_MODEL={available[0]}[/]\n"
                "    [cyan]ankedo setup[/]  [dim](picks all six for you)[/]\n"
            )
            return False
        _line(True, "every configured model is served")

    # ── 4/5/6. A real call, through the real client ─────────────────────────
    console.print("\n[bold]Calls[/]")
    await init_db()
    async with get_session() as session:
        try:
            client = LLMClient(session)
        except LLMError as exc:
            _line(False, "client would not construct", str(exc))
            return False

        model = configured["chat_agent"]
        try:
            result = await client.generate(
                model=model,
                prompt="Reply with the word OK.",
                schema=_Probe,
                purpose="llm-check",
                prompt_version="check-v1",
            )
        except LLMError as exc:
            _line(False, f"structured call to {model} failed")
            console.print(f"\n  [red]{exc}[/]\n")
            _hint(str(exc))
            return False

        _line(True, f"{model} answered", f"answer={result.answer!r}")

    console.print(
        "\n[green bold]✓ The model path works.[/] "
        "[dim]The ledger now has a real call in it.[/]\n"
    )
    return True


def _hint(error: str) -> None:
    """Turn the provider's error into the thing to do about it."""
    low = error.lower()
    if "404" in low or "not found" in low or "does not exist" in low:
        console.print("  [dim]The model name is wrong for this endpoint. "
                      "Run `ankedo configure list-models`.[/]\n")
    elif "401" in low or "403" in low or "unauthor" in low:
        console.print("  [dim]The key was rejected. Check it with "
                      "`ankedo configure list-models`.[/]\n")
    elif "connect" in low or "timeout" in low or "refused" in low:
        console.print("  [dim]The endpoint did not answer. Is the proxy running, and is\n"
                      "  the address reachable from this machine? 0.0.0.0 is not an\n"
                      "  address a client can dial — use the host's real IP.[/]\n")
    elif "schema" in low or "json" in low or "parse" in low or "response_format" in low:
        console.print("  [dim]The model would not produce structured output. The whole\n"
                      "  committee parses responses into schemas, so pick a model that\n"
                      "  supports it — `ankedo configure list-models` marks those that\n"
                      "  do not.[/]\n")


def main() -> bool:
    return asyncio.run(run_llm_check())

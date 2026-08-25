"""
Interactive setup wizard for AnkEdo — Hermes/OpenClaw-style first-run configuration.

Usage:
    ankedo setup              # Interactive wizard
    ankedo setup --reconfigure # Re-run on existing config
    ankedo setup --non-interactive  # Headless (all config from env/flags)
"""
from __future__ import annotations

import os
import secrets
import sys
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

PROVIDERS = {
    "openai": {
        "name": "OpenAI",
        "key_env": "OPENAI_API_KEY",
        "key_prefix": "sk-",
        "models": {
            "triage": "gpt-4o-mini",
            "specialist": "gpt-4o",
            "critic": "gpt-4o-mini",
            "chat": "gpt-4o-mini",
        },
        "validate_url": "https://api.openai.com/v1/models",
    },
    "anthropic": {
        "name": "Anthropic",
        "key_env": "ANTHROPIC_API_KEY",
        "key_prefix": "sk-ant-",
        "models": {
            "triage": "claude-3-5-haiku-20241022",
            "specialist": "claude-sonnet-4-20250514",
            "critic": "claude-3-5-haiku-20241022",
            "chat": "claude-3-5-haiku-20241022",
        },
        "validate_url": "https://api.anthropic.com/v1/messages",
    },
    "custom": {
        "name": "Custom (OpenAI-compatible)",
        "key_env": "OPENAI_API_KEY",
        "key_prefix": "",
        "models": {
            "triage": "default",
            "specialist": "default",
            "critic": "default",
            "chat": "default",
        },
        "validate_url": None,
    },
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

    try:
        if provider_id == "openai" or provider_id == "custom":
            url = base_url or "https://api.openai.com/v1/models"
            resp = httpx.get(
                url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10
            )
            return resp.status_code == 200
        elif provider_id == "anthropic":
            resp = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-3-5-haiku-20241022",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=15,
            )
            return resp.status_code in (200, 201)
    except Exception as e:
        console.print(f"[yellow]⚠ Validation request failed: {e}[/]")
        return False
    return False


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
                config[key.strip()] = value.strip()
    return config


def _write_env(config: dict[str, str]):
    """Write config dict to .env file, preserving comments from .env.example."""
    lines: list[str] = []

    if ENV_EXAMPLE.exists():
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines.append(line)
            elif "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                value = config.get(key, stripped.split("=", 1)[1].strip())
                lines.append(f"{key}={value}")
            else:
                lines.append(line)
    else:
        for key, value in config.items():
            lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_setup(non_interactive: bool = False, reconfigure: bool = False):
    """Main setup wizard entry point."""
    _banner()

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
        # Validate required keys from environment
        required = ["OPENAI_API_KEY"]
        missing = [k for k in required if not os.environ.get(k) and not config.get(k)]
        if missing:
            console.print(f"[red]✗ Missing required environment variables: {', '.join(missing)}[/]")
            console.print("[dim]Set them in your environment or .env file before running --non-interactive[/]")
            sys.exit(1)

        for key in os.environ:
            upper = key.upper()
            if upper.startswith(("OPENAI_", "ANTHROPIC_", "TELEGRAM_", "WHATSAPP_",
                                 "DATABASE_", "LOG_", "API_", "MCP_", "SECRET_",
                                 "TRIAGE_", "SPECIALIST_", "CRITIC_", "CHAT_AGENT_",
                                 "AUTO_", "PACING_", "SESSION_")):
                config[upper] = os.environ[key]

        if not config.get("SECRET_KEY"):
            config["SECRET_KEY"] = secrets.token_hex(32)

        _write_env(config)
        console.print("[green]✓ Configuration saved from environment variables.[/]")
        return

    # ── Step 1: AI Provider ──────────────────────────────────────────────
    _step_header(1, total_steps, "AI Provider")

    console.print("Which AI provider would you like to use?\n")
    provider_choices = list(PROVIDERS.keys())
    for i, pid in enumerate(provider_choices, 1):
        p = PROVIDERS[pid]
        marker = "→ " if i == 1 else "  "
        console.print(f"  {marker}[bold]{i}[/]. {p['name']}")
    console.print()

    provider_idx = Prompt.ask(
        "Select provider",
        choices=[str(i) for i in range(1, len(provider_choices) + 1)],
        default="1",
    )
    provider_id = provider_choices[int(provider_idx) - 1]
    provider = PROVIDERS[provider_id]
    console.print(f"\n[green]✓ Selected: {provider['name']}[/]")

    # Custom base URL for custom providers
    custom_base_url = None
    if provider_id == "custom":
        custom_base_url = Prompt.ask(
            "\nEnter the API base URL (e.g., http://localhost:11434/v1)",
        )
        config["OPENAI_API_BASE"] = custom_base_url

    # ── Step 2: API Key ──────────────────────────────────────────────────
    _step_header(2, total_steps, "API Key")

    existing_key = config.get(provider["key_env"], "")
    if existing_key and existing_key not in ("sk-...", "sk-ant-..."):
        console.print(f"[dim]Current key: {_mask_key(existing_key)}[/]")
        if not Confirm.ask("Update this key?", default=False):
            api_key = existing_key
        else:
            api_key = Prompt.ask(f"Enter your {provider['name']} API key")
    else:
        hint = f" (starts with {provider['key_prefix']})" if provider["key_prefix"] else ""
        api_key = Prompt.ask(f"Enter your {provider['name']} API key{hint}")

    # Validate
    console.print("[dim]Validating API key...[/]", end=" ")
    validate_url = custom_base_url + "/models" if custom_base_url else None
    if _validate_api_key(provider_id, api_key, validate_url):
        console.print("[green]✓ Key is valid![/]")
    else:
        console.print("[yellow]⚠ Could not validate key (network issue or invalid key)[/]")
        if not Confirm.ask("Continue anyway?", default=True):
            sys.exit(1)

    config[provider["key_env"]] = api_key

    # Set models based on provider
    config["TRIAGE_MODEL"] = provider["models"]["triage"]
    config["SPECIALIST_MODEL"] = provider["models"]["specialist"]
    config["CRITIC_MODEL"] = provider["models"]["critic"]
    config["CHAT_AGENT_MODEL"] = provider["models"]["chat"]

    # ── Step 3: Model Configuration ──────────────────────────────────────
    _step_header(3, total_steps, "Model Configuration")

    model_table = Table(box=box.SIMPLE_HEAVY, show_header=True)
    model_table.add_column("Role", style="cyan")
    model_table.add_column("Model", style="green")
    model_table.add_row("Triage Agent", config["TRIAGE_MODEL"])
    model_table.add_row("Specialist Agent", config["SPECIALIST_MODEL"])
    model_table.add_row("Critic Agent", config["CRITIC_MODEL"])
    model_table.add_row("Chat Agent", config["CHAT_AGENT_MODEL"])
    console.print(model_table)
    console.print()

    if Confirm.ask("Customize model assignments?", default=False):
        config["TRIAGE_MODEL"] = Prompt.ask("Triage model", default=config["TRIAGE_MODEL"])
        config["SPECIALIST_MODEL"] = Prompt.ask("Specialist model", default=config["SPECIALIST_MODEL"])
        config["CRITIC_MODEL"] = Prompt.ask("Critic model", default=config["CRITIC_MODEL"])
        config["CHAT_AGENT_MODEL"] = Prompt.ask("Chat model", default=config["CHAT_AGENT_MODEL"])
        console.print("[green]✓ Models updated[/]")
    else:
        console.print("[green]✓ Using defaults[/]")

    # ── Step 4: Notification Channels ────────────────────────────────────
    _step_header(4, total_steps, "Notification Channels (Optional)")

    console.print("[dim]The agent can notify you via Telegram or WhatsApp.")
    console.print("You can skip this and configure later with: ankedo configure[/]\n")

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

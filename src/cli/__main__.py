"""
CLI entry point for AnkEdo.

Usage:
    ankedo setup        — Interactive first-run configuration wizard
    ankedo doctor       — System health check
    ankedo start        — Launch agent + dashboard
    ankedo db init      — Initialize the database
    ankedo cases add    — Register a new case
    ankedo accounts add — Add a worker account
    ankedo configure    — Re-configure specific settings
    ankedo update       — Pull latest code, rebuild what changed, then check health
"""
import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

import click
import structlog

from src.core.logging_config import configure_logging

log = structlog.get_logger()
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@click.group()
def main():
    """AnkEdo — AI-Powered Hate Speech Monitoring Agent"""
    configure_logging()


# ═══════════════════════════════════════════════════════════════════════════
# Setup & Configuration
# ═══════════════════════════════════════════════════════════════════════════

@main.command(name="setup")
@click.option("--non-interactive", is_flag=True, help="Run without prompts (use env vars)")
@click.option("--reconfigure", is_flag=True, help="Discard existing config and start fresh")
def setup_cmd(non_interactive: bool, reconfigure: bool):
    """Interactive setup wizard — configure AI provider, API keys, and channels."""
    from src.cli.setup_wizard import run_setup
    run_setup(non_interactive=non_interactive, reconfigure=reconfigure)


@main.group(name="configure", invoke_without_command=True)
@click.pass_context
def configure_group(ctx):
    """Inspect or change configuration without re-running the whole wizard."""
    if ctx.invoked_subcommand is None:
        from src.cli.setup_wizard import run_setup
        run_setup(reconfigure=True)


@configure_group.command(name="models")
def configure_models_cmd():
    """Show the model assigned to each agent role."""
    from src.cli.setup_wizard import show_models
    show_models()


@configure_group.command(name="list-models")
def configure_list_models_cmd():
    """List the models the configured provider actually serves."""
    from src.cli.setup_wizard import list_available_models
    list_available_models()


@configure_group.command(name="set")
@click.argument("pairs", nargs=-1, required=True, metavar="KEY=VALUE...")
def configure_set_cmd(pairs: tuple[str, ...]):
    """Set one or more .env values.

    \b
    ankedo configure set SPECIALIST_MODEL=gemini-3.6-flash
    ankedo configure set TRIAGE_MODEL=gemini-3.5-flash-lite LOG_LEVEL=DEBUG
    """
    from src.cli.setup_wizard import set_env_values
    set_env_values(pairs)


# ═══════════════════════════════════════════════════════════════════════════
# Health Check
# ═══════════════════════════════════════════════════════════════════════════

@main.command(name="doctor")
@click.option("--fix", is_flag=True, help="Auto-fix what can be fixed")
def doctor_cmd(fix: bool):
    """Run system health check — validates all components."""
    from src.cli.health_check import run_doctor
    success = run_doctor(fix=fix)
    sys.exit(0 if success else 1)


@main.command(name="test-llm")
def test_llm_cmd():
    """Make one real model call and report exactly where it breaks."""
    from src.cli.llm_check import main as run_check
    sys.exit(0 if run_check() else 1)


@main.command(name="token")
@click.option("--new", "rotate", is_flag=True, help="Replace the existing token.")
def token_cmd(rotate: bool):
    """Show the dashboard's admin token, creating one if there is none.

    The dashboard asks for this the first time you open it. It is the only thing
    standing between anyone who can reach the port and a database of verdicts about
    named people, so the agent refuses every request until it exists rather than
    serving that unauthenticated.
    """
    import secrets

    from rich.console import Console

    from src.cli.setup_wizard import _load_existing_env, _write_env
    from src.core.settings import get_settings

    console = Console()

    config = _load_existing_env()
    if not config:
        console.print("[red]No .env found.[/] Run [cyan]ankedo setup[/] first.")
        sys.exit(1)

    existing = config.get("ADMIN_API_TOKEN")
    if existing and not rotate:
        console.print(f"\n  [bold]{existing}[/]\n")
        console.print("[dim]Paste this into the dashboard when it asks.[/]")
        console.print("[dim]Rotate it with: ankedo token --new[/]")
        return

    config["ADMIN_API_TOKEN"] = secrets.token_urlsafe(24)
    _write_env(config)
    get_settings.cache_clear()

    console.print(f"\n  [bold green]{config['ADMIN_API_TOKEN']}[/]\n")
    if existing:
        console.print("[yellow]Rotated.[/] Every signed-in dashboard must paste the new one.")
    console.print("[dim]Restart the agent for it to take effect.[/]")


# ═══════════════════════════════════════════════════════════════════════════
# Start / Stop
# ═══════════════════════════════════════════════════════════════════════════

@main.command(name="start")
@click.option("--host", default=None, help="API host (default: from .env or 127.0.0.1)")
@click.option("--port", default=None, type=int, help="API port (default: from .env or 8000)")
@click.option("--no-browser", is_flag=True, help="Don't open the browser automatically")
def start_cmd(host: str | None, port: int | None, no_browser: bool):
    """Start the AnkEdo agent and web dashboard."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm
    from rich.text import Text
    console = Console()

    # Load settings
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        console.print("[red]✗ No .env file found. Run 'ankedo setup' first.[/]")
        sys.exit(1)

    from src.core.settings import get_settings
    settings = get_settings()

    api_host = host or settings.api_host
    api_port = port or settings.api_port

    # 8000 is a popular port. Find out before announcing a dashboard and opening a
    # browser at it — otherwise the operator is sent to whatever else is listening,
    # and uvicorn's bind error scrolls past under a panel that said it was running.
    # Connect rather than bind: on Windows SO_REUSEADDR lets a probe bind a port that
    # another process is already serving, so a bind test quietly passes and uvicorn
    # fails later — under a panel that has already said the dashboard is up.
    import socket

    probe_host = "127.0.0.1" if api_host in ("0.0.0.0", "") else api_host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex((probe_host, api_port)) == 0:
            console.print(f"\n[red]✗ Port {api_port} is already serving something.[/]")
            console.print(
                f"\n[dim]Open http://{probe_host}:{api_port} to see what. If it is an old\n"
                "AnkEdo, stop it first. Otherwise pick another port:[/]\n"
                f"  [cyan]ankedo start --port {api_port + 1}[/]        [dim]just this run[/]\n"
                f"  [cyan]ankedo configure set API_PORT={api_port + 1}[/]  [dim]permanently[/]\n"
            )
            sys.exit(1)

    # The dashboard is a built artefact and dist/ is gitignored, so a fresh checkout
    # has none. Announcing a URL that serves nothing is how an operator concludes the
    # whole agent is broken.
    dist_index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if not dist_index.exists():
        console.print("\n[yellow]⚠ The dashboard has not been built.[/]")
        # Only offer when someone is there to answer: Confirm.ask on a closed stdin
        # re-prompts until it aborts, which is how `curl | bash` used to die.
        can_ask = sys.stdin.isatty()
        if (PROJECT_ROOT / "frontend" / "package.json").exists() and shutil.which("npm"):
            if can_ask and Confirm.ask("  Build it now? (takes a minute or two)", default=True):
                frontend = PROJECT_ROOT / "frontend"

                def _npm(*args):
                    return subprocess.run(
                        ["npm", *args], cwd=frontend, capture_output=True, text=True,
                        shell=(os.name == "nt"),
                    )

                # Build before install is the usual reason this fails on a fresh
                # machine: dist/ is gitignored, and so is node_modules.
                if not (frontend / "node_modules").exists():
                    console.print("[dim]  Installing frontend dependencies...[/]", end=" ")
                    installed = _npm("install", "--no-audit", "--no-fund")
                    console.print("[green]✓[/]" if installed.returncode == 0 else "[red]✗[/]")

                console.print("[dim]  Building...[/]", end=" ")
                built = _npm("run", "build")
                if built.returncode == 0 and dist_index.exists():
                    console.print("[green]✓[/]")
                else:
                    console.print("[red]✗[/]")
                    # The last line is usually "npm ERR! ..." and says nothing. Show
                    # the real complaint, which is where an unsupported Node version
                    # or an out-of-memory kill actually appears.
                    output = ((built.stderr or "") + "\n" + (built.stdout or "")).strip()
                    interesting = [
                        line for line in output.splitlines()
                        if line.strip() and not line.startswith("npm notice")
                    ]
                    for line in interesting[-12:]:
                        console.print(f"[dim]    {line[:160]}[/]")
        if not dist_index.exists():
            console.print(
                "[dim]  The API and /docs still work. To build it later:[/]\n"
                "[dim]    cd frontend && npm install && npm run build[/]"
            )

    dashboard_line = (
        f"[dim]Dashboard:[/] [bold]http://{api_host}:{api_port}[/]\n"
        if dist_index.exists()
        else "[dim]Dashboard:[/] [yellow]not built[/]\n"
    )

    console.print()
    console.print(
        Panel(
            Text.from_markup(
                "[bold cyan]🔺 AnkEdo — Starting Agent[/]\n\n"
                + dashboard_line
                + f"[dim]API Docs:[/]  [bold]http://{api_host}:{api_port}/docs[/]\n"
                # Say whether the agent is actually running. This printed a dashboard
                # URL and nothing else while collecting nothing at all.
                + (
                    f"[dim]Agent:[/]     [green]running[/] "
                    f"[dim](every {settings.loop_interval_seconds}s)[/]\n"
                    if settings.run_agent_with_api
                    else "[dim]Agent:[/]     [yellow]not running[/] "
                         "[dim](RUN_AGENT_WITH_API=false)[/]\n"
                )
                + "[dim]Press Ctrl+C to stop[/]"
            ),
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print()

    # Ensure data directories exist
    for d in ["data", "evidence", "logs", "screenshots"]:
        (PROJECT_ROOT / d).mkdir(exist_ok=True)

    # Several settings are relative paths documented as "relative to the project" —
    # evidence_dir, log_dir, the pack directory, and Camoufox's ./sessions profiles.
    # Running from elsewhere would scatter them through the operator's home directory.
    os.chdir(PROJECT_ROOT)

    # Initialize database if needed
    db_path = PROJECT_ROOT / "data" / "ankedo.db"
    if not db_path.exists():
        console.print("[dim]Initializing database...[/]", end=" ")
        try:
            from src.core.database import init_db
            asyncio.run(init_db())
            console.print("[green]✓[/]")
        except Exception as e:
            console.print(f"[yellow]⚠ {e}[/]")

    # Open browser
    # Only when there is something to show; /docs is not what they asked for.
    if not no_browser and dist_index.exists():
        import webbrowser
        import threading
        def _open():
            import time
            time.sleep(2)
            webbrowser.open(f"http://{api_host}:{api_port}")
        threading.Thread(target=_open, daemon=True).start()

    # Start uvicorn
    import uvicorn
    try:
        uvicorn.run(
            "src.api.main:app",
            host=api_host,
            port=api_port,
            reload=False,
            log_level="info",
        )
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/]")
    except OSError as exc:
        # Belt and braces: the pre-flight check above catches the common case, but the
        # port can be taken in the moment between checking and binding.
        console.print(f"\n[red]✗ Could not start the server: {exc}[/]")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════
# Update
# ═══════════════════════════════════════════════════════════════════════════

@main.command(name="update")
@click.option("--skip-deps", is_flag=True, help="Skip the dependency step entirely")
@click.option("--force-deps", is_flag=True, help="Reinstall dependencies even if unchanged")
@click.option("--fix", is_flag=True, help="Repair anything the health check finds broken")
def update_cmd(skip_deps: bool, force_deps: bool, fix: bool):
    """Pull the latest code and bring everything else back into step.

    One command, because "update" that leaves the dashboard stale and the browser
    missing is not an update — it is a git pull with extra steps. What actually needs
    redoing is decided from what the pull changed, so a routine update that touches
    only Python is quick.
    """
    from rich.console import Console
    console = Console()

    console.print("\n[bold cyan]🔺 AnkEdo — Update[/]\n")

    def run(args, **kwargs):
        return subprocess.run(
            args, cwd=str(kwargs.pop("cwd", PROJECT_ROOT)),
            capture_output=True, text=True, **kwargs,
        )

    # ── Code ────────────────────────────────────────────────────────────────
    before = run(["git", "rev-parse", "HEAD"]).stdout.strip()

    console.print("[dim]Pulling latest code...[/]", end=" ")
    try:
        pulled = run(["git", "pull", "--ff-only", "origin", "master"])
    except FileNotFoundError:
        console.print("[red]✗ Git not found. Install git and try again.[/]")
        sys.exit(1)

    if pulled.returncode != 0:
        console.print("[red]✗[/]")
        console.print(f"[dim]  {(pulled.stderr or pulled.stdout).strip()[:300]}[/]")
        console.print(
            "\n[dim]A fast-forward was refused — usually local commits or edited files.[/]\n"
            "[dim]  git -C " + str(PROJECT_ROOT) + " status[/]\n"
        )
        sys.exit(1)

    after = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if before == after:
        console.print("[green]✓[/] [dim]already up to date[/]")
        changed: set[str] = set()
    else:
        console.print(f"[green]✓[/] [dim]{before[:7]} → {after[:7]}[/]")
        changed = {
            line.strip()
            for line in run(["git", "diff", "--name-only", before, after]).stdout.splitlines()
            if line.strip()
        }

    def touched(prefix: str) -> bool:
        return any(path.startswith(prefix) for path in changed)

    # ── Dependencies ────────────────────────────────────────────────────────
    # Only when the manifest moved or the installed set is inconsistent. Three
    # minutes of reinstalling what is already present, on every update, is the
    # complaint that prompted this.
    if skip_deps:
        console.print("[dim]Dependencies:[/] [dim]skipped[/]")
    else:
        stale = force_deps or "pyproject.toml" in changed
        if not stale:
            stale = run([sys.executable, "-m", "pip", "check"]).returncode != 0
        if not stale:
            console.print("[dim]Dependencies:[/] [green]✓[/] [dim]unchanged[/]")
        else:
            console.print("[dim]Installing dependencies...[/]", end=" ")
            installed = run([sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"])
            console.print("[green]✓[/]" if installed.returncode == 0
                          else f"[yellow]⚠ {installed.stderr.strip()[:120]}[/]")

    # ── Database ────────────────────────────────────────────────────────────
    console.print("[dim]Checking database schema...[/]", end=" ")
    try:
        from src.core.database import init_db
        asyncio.run(init_db())
        console.print("[green]✓[/]")
    except Exception as exc:
        console.print(f"[yellow]⚠ {exc}[/]")

    # ── Dashboard ───────────────────────────────────────────────────────────
    # dist/ is gitignored, so a pull never updates it: without this step an update
    # that changed the frontend leaves the old bundle being served, and a fresh
    # checkout serves nothing at all.
    dist_index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    frontend = PROJECT_ROOT / "frontend"
    # frontend/dist is committed, so the pull usually brings the built bundle with it
    # and nothing needs building here at all. A rebuild is only for a checkout whose
    # source moved without the bundle — a developer mid-change, not an operator.
    source_changed = any(
        path.startswith(("frontend/src/", "frontend/index.html", "frontend/package"))
        for path in changed
    )
    if not frontend.exists():
        pass
    elif dist_index.exists() and not source_changed:
        console.print("[dim]Dashboard:[/] [green]✓[/] [dim]up to date[/]")
    elif not shutil.which("npm"):
        console.print(
            "[dim]Dashboard:[/] [yellow]⚠ needs a rebuild but npm is not installed[/]"
        )
    else:
        why = "not built yet" if not dist_index.exists() else "source changed"
        console.print(f"[dim]Rebuilding the dashboard ({why})...[/]", end=" ")
        if not (frontend / "node_modules").exists():
            run(["npm", "install", "--no-audit", "--no-fund"],
                cwd=frontend, shell=(os.name == "nt"))
        built = run(["npm", "run", "build"], cwd=frontend, shell=(os.name == "nt"))
        if built.returncode == 0 and dist_index.exists():
            console.print("[green]✓[/]")
        else:
            console.print("[red]✗[/]")
            output = ((built.stderr or "") + "\n" + (built.stdout or "")).strip()
            for line in [ln for ln in output.splitlines() if ln.strip()][-8:]:
                console.print(f"[dim]    {line[:150]}[/]")

    # ── Health ──────────────────────────────────────────────────────────────
    # Ending on the doctor is the point: an update that quietly left the browser
    # missing looked successful right up until the first collection pass.
    console.print()
    from src.cli.health_check import run_doctor

    healthy = run_doctor(fix=fix)

    if healthy:
        console.print("[green bold]✓ Update complete.[/] [dim]Run 'ankedo start'.[/]\n")
    elif not fix:
        console.print("[dim]Run 'ankedo update --fix' to repair what can be repaired.[/]\n")
    sys.exit(0 if healthy else 1)


# ═══════════════════════════════════════════════════════════════════════════
# Database
# ═══════════════════════════════════════════════════════════════════════════

@main.group(name="db")
def db_group():
    """Database management commands"""
    pass


@db_group.command(name="init")
def db_init():
    """Initialize the database schema (runs migrations)."""
    db_upgrade.callback()


@db_group.command(name="upgrade")
def db_upgrade():
    """Apply pending migrations. Safe on a database with data."""
    from rich.console import Console

    console = Console()
    console.print("[dim]Applying migrations...[/]", end=" ")
    try:
        from src.core.database import upgrade_db

        upgrade_db()
        console.print("[green]✓ Schema up to date.[/]")
    except Exception as exc:
        console.print(f"[red]✗ Failed: {exc}[/]")
        sys.exit(1)


@db_group.command(name="encrypt-credentials")
def db_encrypt_credentials():
    """Encrypt credentials that predate encryption being implemented."""
    from rich.console import Console

    console = Console()

    async def _migrate():
        from sqlalchemy import select

        from src.core.crypto import encrypt, is_encrypted
        from src.core.database import get_session
        from src.models.agent_worker_account import AgentWorkerAccount
        from src.models.channel_config import ChannelConfig

        moved = 0
        async with get_session() as session:
            for row in (await session.execute(select(ChannelConfig))).scalars():
                if not is_encrypted(row.encrypted_credentials):
                    row.encrypted_credentials = encrypt(row.encrypted_credentials)
                    moved += 1
            for row in (await session.execute(select(AgentWorkerAccount))).scalars():
                if not is_encrypted(row.password_encrypted):
                    row.password_encrypted = encrypt(row.password_encrypted)
                    moved += 1
        return moved

    from src.core.crypto import CryptoError

    try:
        moved = asyncio.run(_migrate())
    except CryptoError as exc:
        console.print(f"[red]✗ {exc}[/]")
        sys.exit(1)

    console.print(
        f"[green]✓ Encrypted {moved} credential(s).[/]"
        if moved
        else "[dim]Nothing to encrypt — all credentials are already protected.[/]"
    )


@main.command(name="backup")
@click.argument("destination", type=click.Path(path_type=Path), required=False)
def backup_cmd(destination: Path | None):
    """Back up the database and evidence.

    Cheap, and a lost case history is unrecoverable — the evidence documents events
    that already happened to people who cannot be asked to reproduce them.
    """
    import shutil
    from datetime import datetime, timezone

    from rich.console import Console

    console = Console()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination or (PROJECT_ROOT / "backups" / stamp)
    destination.mkdir(parents=True, exist_ok=True)

    copied = []
    database = PROJECT_ROOT / "data" / "ankedo.db"
    if database.exists():
        # SQLite's own backup API is consistent under concurrent writes; a file copy
        # of a live database can capture a torn page.
        import sqlite3

        with sqlite3.connect(database) as source, sqlite3.connect(destination / "ankedo.db") as target:
            source.backup(target)
        copied.append("database")

    for name in ("evidence", "screenshots"):
        folder = PROJECT_ROOT / name
        if folder.exists() and any(folder.iterdir()):
            shutil.copytree(folder, destination / name, dirs_exist_ok=True)
            copied.append(name)

    console.print(f"[green]✓ Backed up {', '.join(copied) or 'nothing'} to[/] {destination}")
    console.print("[dim]Sessions and .env are deliberately excluded — they hold live credentials.[/]")


@db_group.command(name="current")
def db_current():
    """Show the migration the database is currently at."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    command.current(cfg, verbose=True)


# ═══════════════════════════════════════════════════════════════════════════
# Agent
# ═══════════════════════════════════════════════════════════════════════════

@main.group(name="agent")
def agent_group():
    """Agent execution commands"""
    pass


@agent_group.command(name="run")
@click.option("--continuous", is_flag=True, help="Run continuously in a loop")
@click.option("--cycles", type=int, default=1, help="Number of cycles to run if not continuous")
def agent_run(continuous: bool, cycles: int):
    """Start the monitoring agent orchestration loop."""
    from rich.console import Console

    console = Console()
    log.info("Starting agent run", continuous=continuous, cycles=cycles)

    async def _run():
        from src.core.database import get_session
        from src.core.orchestration_loop import OrchestrationLoop

        async with get_session() as session:
            loop = OrchestrationLoop(session)
            if continuous:
                await loop.run_forever()
            else:
                for cycle in range(cycles):
                    log.info("Cycle", number=cycle + 1, of=cycles)
                    await loop.run_cycle()

    from src.core.budget import BudgetExceededError

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped.[/]")
    except BudgetExceededError as exc:
        # A guardrail, not a crash — say so plainly so it is not mistaken for a bug.
        console.print(f"[yellow]⚠ Budget guard halted the run:[/] {exc}")
        sys.exit(3)

    log.info("Agent run complete")


# ═══════════════════════════════════════════════════════════════════════════
# Evaluation
# ═══════════════════════════════════════════════════════════════════════════

@main.group(name="eval")
def eval_group():
    """Gold-set evaluation (FR-LE-3, FR-LE-4)"""
    pass


@eval_group.command(name="load")
@click.argument("path", type=click.Path(exists=True, path_type=Path), required=False)
def eval_load(path: Path | None):
    """Import the gold evaluation set from a pack's gold_eval.jsonl."""
    from rich.console import Console

    from src.core.settings import get_settings

    console = Console()
    path = path or Path(get_settings().default_pack_dir) / "gold_eval.jsonl"

    async def _load():
        from src.core.database import get_session
        from src.learning.gold_eval_loader import GoldEvalLoader

        async with get_session() as session:
            return await GoldEvalLoader(session).load_from_jsonl(path)

    result = asyncio.run(_load())
    console.print(
        f"[green]✓[/] loaded {result['created']} new, {result['updated']} updated"
        + (f", [yellow]{result['skipped']} skipped[/]" if result["skipped"] else "")
    )
    for error in result["errors"]:
        console.print(f"[yellow]⚠[/] {error}")


@eval_group.command(name="run")
@click.option("--limit", type=int, default=None, help="Only evaluate N items")
@click.option("--hard-only", is_flag=True, help="Only the minimal pairs and hard cases")
@click.option("--min-precision", type=float, default=0.80, show_default=True)
@click.option("--min-recall", type=float, default=0.80, show_default=True)
def eval_run(limit, hard_only, min_precision, min_recall):
    """Score the classifier against the gold set. Non-zero exit below threshold."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    async def _run():
        from src.core.database import get_session
        from src.learning.evaluator import run_eval

        async with get_session() as session:
            return await run_eval(session, limit=limit, hard_only=hard_only)

    from src.classifiers.llm_client import LLMError
    from src.core.budget import BudgetExceededError

    try:
        report = asyncio.run(_run())
    except BudgetExceededError as exc:
        console.print(f"[red]✗ Budget guard:[/] {exc}")
        sys.exit(3)
    except LLMError as exc:
        console.print(f"[red]✗ {exc}[/]")
        sys.exit(1)

    if report.errors:
        for error in report.errors[:10]:
            console.print(f"[yellow]⚠[/] {error}")
        if not report.overall.total:
            sys.exit(1)

    table = Table(title="Gold set evaluation")
    table.add_column("Slice")
    table.add_column("N", justify="right")
    table.add_column("Precision", justify="right")
    table.add_column("Recall", justify="right")
    table.add_column("F1", justify="right")

    def add(label, metrics, style=""):
        if metrics.total:
            table.add_row(
                f"[{style}]{label}[/]" if style else label,
                str(metrics.total),
                f"{metrics.precision:.2f}",
                f"{metrics.recall:.2f}",
                f"{metrics.f1:.2f}",
            )

    add("overall", report.overall, "bold")
    for group, metrics in sorted(report.by_group.items()):
        add(f"  group: {group}", metrics)
    for dialect, metrics in sorted(report.by_dialect.items()):
        add(f"  dialect: {dialect}", metrics)
    add("hard cases", report.hard_cases, "cyan")
    add("counter-speech", report.counter_speech, "cyan")
    console.print(table)

    if report.overall.abstained:
        console.print(f"[dim]{report.overall.abstained} routed to human review (ambiguous)[/]")

    for failure in report.failures[:10]:
        kind = "false positive" if failure["kind"] == "fp" else "false negative"
        marker = " [cyan](hard case)[/]" if failure["hard_case"] else ""
        console.print(f"\n[red]{kind}[/]{marker} — expected {failure['expected']}, got {failure['got']}")
        console.print(f"  [dim]comment:[/] {failure['text']}")
        console.print(f"  [dim]on post:[/] {failure['parent_post']}")
        if failure["model_rationale"]:
            console.print(f"  [dim]model said:[/] {failure['model_rationale']}")

    passed = report.meets(min_precision, min_recall)
    console.print(
        f"\n[green bold]✓ PASS[/]" if passed
        else f"\n[red bold]✗ FAIL[/] — every group must reach "
             f"precision {min_precision} and recall {min_recall}"
    )
    sys.exit(0 if passed else 1)


@eval_group.command(name="calibrate")
@click.option("--limit", type=int, default=None, help="Only score N items")
def eval_calibrate(limit):
    """Fit confidence calibration against the gold set.

    Until this runs, `auto_flag_threshold` is compared against a raw model score,
    which is systematically overconfident and so does not mean what it appears to.
    """
    from rich.console import Console
    from rich.table import Table

    console = Console()

    async def _run():
        from sqlalchemy import select

        from src.classifiers.committee.orchestrator import CommitteeOrchestrator
        from src.classifiers.context_bundle import ContextBundle
        from src.core.database import get_session
        from src.learning.calibration import calibrate
        from src.models.gold_eval_entry import GoldEvalEntry

        async with get_session() as session:
            stmt = select(GoldEvalEntry).limit(limit) if limit else select(GoldEvalEntry)
            entries = (await session.execute(stmt)).scalars().all()
            orchestrator = CommitteeOrchestrator(session)

            scored = []
            for entry in entries:
                bundle = ContextBundle(
                    comment_text=entry.text_content,
                    parent_post_text=entry.parent_post_text or "",
                    target_groups=[entry.target_group] if entry.target_group else [],
                    dialect=entry.dialect,
                )
                scored.append((entry, await orchestrator.run(bundle)))
            return await calibrate(session, scored)

    from src.classifiers.llm_client import LLMError
    from src.core.budget import BudgetExceededError

    try:
        report = asyncio.run(_run())
    except BudgetExceededError as exc:
        console.print(f"[red]✗ Budget guard:[/] {exc}")
        sys.exit(3)
    except LLMError as exc:
        console.print(f"[red]✗ {exc}[/]")
        sys.exit(1)

    if report.samples < 10:
        console.print(
            f"[yellow]⚠ only {report.samples} decisive items — too few to calibrate.[/]\n"
            "[dim]Fitting on a handful produces a confident, meaningless number.[/]"
        )
        sys.exit(1)

    console.print(f"\n[bold]{report.summary()}[/]\n")

    table = Table(title="Reliability")
    table.add_column("Confidence")
    table.add_column("N", justify="right")
    table.add_column("Claimed", justify="right")
    table.add_column("Actual", justify="right")
    for row in report.bins:
        gap = abs(row["claimed"] - row["actual"])
        style = "red" if gap > 0.15 else ""
        table.add_row(
            row["range"], str(row["count"]),
            f"{row['claimed']:.2f}",
            f"[{style}]{row['actual']:.2f}[/]" if style else f"{row['actual']:.2f}",
        )
    console.print(table)

    if not report.improved:
        console.print("[yellow]⚠ Calibration did not improve — the model may already be calibrated.[/]")


@eval_group.command(name="kappa")
def eval_kappa():
    """Inter-rater agreement on doubly-annotated items."""
    from rich.console import Console
    from sqlalchemy import select

    console = Console()

    async def _kappa():
        from src.core.database import get_session
        from src.learning.evaluator import cohens_kappa
        from src.models.gold_eval_entry import GoldEvalEntry

        async with get_session() as session:
            entries = (await session.execute(select(GoldEvalEntry))).scalars().all()
            return cohens_kappa(list(entries))

    kappa, n = asyncio.run(_kappa())
    if kappa is None:
        console.print(
            f"[yellow]⚠ only {n} doubly-annotated items — need at least 2.[/]\n"
            "[dim]Without agreement data the gold set cannot be trusted as ground truth.[/]"
        )
        sys.exit(1)

    console.print(f"Cohen's κ = [bold]{kappa:.2f}[/] over {n} items")
    if kappa < 0.6:
        console.print(
            "[red]Below 0.6 — the labelling definition is the problem, not the model.[/]\n"
            "[dim]Annotators cannot apply the guidance consistently; more data will not help.[/]"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Classification
# ═══════════════════════════════════════════════════════════════════════════

@main.command(name="classify")
@click.option("--text", required=True, help="The comment to classify")
@click.option("--post-text", default="", help="The parent post it replies to")
@click.option("--target-group", default=None, help="Group slug, if known from a case")
@click.option("--dialect", default=None, help="e.g. iraqi, msa, sorani")
@click.option("--json", "as_json", is_flag=True, help="Emit the full trace as JSON")
def classify_cmd(text, post_text, target_group, dialect, as_json):
    """Classify one comment in context, without scraping anything.

    The acceptance test for the whole pipeline: the same text must come out benign on
    an unrelated post and hateful on one concerning the targeted group.
    """
    import json as jsonlib

    from rich.console import Console

    console = Console()

    async def _run():
        from src.classifiers.context_bundle import ContextBundle
        from src.classifiers.committee.orchestrator import CommitteeOrchestrator
        from src.classifiers.group_resolver import GroupResolver
        from src.core.database import get_session, init_db

        await init_db()
        async with get_session() as session:
            groups = [target_group] if target_group else []
            if not groups and post_text:
                groups = await GroupResolver(session).resolve_all(post_text)

            bundle = ContextBundle(
                comment_text=text,
                parent_post_text=post_text,
                target_groups=groups,
                target_group_source="case" if target_group else ("detected" if groups else None),
                dialect=dialect,
            )
            return await CommitteeOrchestrator(session).run(bundle)

    from src.classifiers.llm_client import LLMError
    from src.core.budget import BudgetExceededError

    try:
        result = asyncio.run(_run())
    except BudgetExceededError as exc:
        console.print(f"[red]✗ Budget guard:[/] {exc}")
        sys.exit(3)
    except LLMError as exc:
        console.print(f"[red]✗ {exc}[/]")
        sys.exit(1)

    if as_json:
        console.print_json(jsonlib.dumps(result, ensure_ascii=False, default=str))
        return

    colour = {"hate": "red", "benign": "green", "ambiguous": "yellow"}[result["verdict"]]
    console.print(f"\n[{colour} bold]{result['verdict'].upper()}[/]  "
                  f"confidence {result['confidence']:.2f}  severity {result['severity']}")
    if result["target_group"]:
        console.print(f"[dim]target group:[/] {result['target_group']}")
    if result["relies_on_context"]:
        console.print("[dim]benign in isolation — hostile only in this context[/]")
    if result["committee_disagreement"]:
        console.print("[yellow]⚠ committee disagreed — routed to human review[/]")

    for trope in result["trace"]["tropes_fired"]:
        console.print(f"[dim]trope fired:[/] {trope['trope_id']} — {trope['reason']}")
    for trope in result["trace"]["trope_candidates"]:
        console.print(f"[dim]trope candidate (not fired):[/] {trope['trope_id']} — {trope['reason']}")

    specialist = result["trace"]["specialist"]
    if specialist:
        console.print(f"\n[dim]{specialist['rationale']}[/]")
    console.print()


# ═══════════════════════════════════════════════════════════════════════════
# Platform (Ettok)
# ═══════════════════════════════════════════════════════════════════════════

@main.group(name="platform")
def platform_group():
    """Talk to the Ettok platform (see docs/AGENT_CONTRACT.md)"""
    pass


def _run_with_client(coro_factory):
    """Run a coroutine with a client, turning contract failures into clean exits."""
    from rich.console import Console

    from src.ettok.client import AgentKeyRejected, EttokClient, EttokError

    console = Console()

    async def _run():
        async with EttokClient() as client:
            return await coro_factory(client)

    try:
        return asyncio.run(_run())
    except AgentKeyRejected as exc:
        # Contract: stop and alert, never retry.
        console.print(f"[red]✗ Agent key rejected:[/] {exc}")
        console.print("[dim]Issue a new key in the Django admin under 'Agent keys'.[/]")
        sys.exit(2)
    except EttokError as exc:
        console.print(f"[red]✗ Platform error:[/] {exc}")
        sys.exit(1)


@platform_group.command(name="ping")
def platform_ping():
    """Send a heartbeat and show the config the platform returns."""
    from rich.console import Console

    console = Console()
    data = _run_with_client(lambda c: c.heartbeat(status="idle"))

    console.print("[green]✓ Platform reachable[/]")
    if data.get("scan_requested"):
        console.print("[yellow]⚠ A scan was requested — the flag is one-shot and now consumed.[/]")
    for key, value in (data.get("config") or {}).items():
        console.print(f"  [dim]{key}:[/] {value}")


@platform_group.command(name="sync-lexicon")
@click.option("--language", "languages", multiple=True, help="Filter, e.g. --language ar")
def platform_sync_lexicon(languages: tuple[str, ...]):
    """Pull the platform lexicon into the local cache."""
    from rich.console import Console

    console = Console()

    async def _sync(client):
        from src.core.database import get_session, init_db
        from src.ettok.sync import sync_lexicon

        await init_db()
        async with get_session() as session:
            return await sync_lexicon(session, client, languages=list(languages) or None)

    result = _run_with_client(_sync)

    console.print(
        f"[green]✓ Synced[/] {result.fetched} terms "
        f"([dim]new[/] {result.created}, [dim]updated[/] {result.updated}, "
        f"[dim]deactivated[/] {result.deactivated})"
    )
    for group, count in result.unresolved_groups.items():
        console.print(
            f"[yellow]⚠[/] target group {group!r} ({count} terms) does not resolve to a "
            "canonical group — those terms cannot be gated on context"
        )
    for bad in result.bad_regexes:
        console.print(f"[yellow]⚠[/] skipped uncompilable regex {bad}")


@platform_group.command(name="sync-tropes")
def platform_sync_tropes():
    """Pull the trope dictionary into the local cache."""
    from rich.console import Console

    console = Console()

    async def _sync(client):
        from src.core.database import get_session, init_db
        from src.ettok.sync import sync_tropes

        await init_db()
        async with get_session() as session:
            return await sync_tropes(session, client)

    result = _run_with_client(_sync)

    console.print(
        f"[green]✓ Synced[/] {result.fetched} tropes "
        f"([dim]new[/] {result.created}, [dim]updated[/] {result.updated}, "
        f"[dim]deactivated[/] {result.deactivated})"
    )
    for group, count in result.unresolved_groups.items():
        console.print(f"[yellow]⚠[/] target group {group!r} ({count}) did not resolve")
    for warning in result.bad_regexes:
        console.print(f"[yellow]⚠[/] {warning}")
    if result.bad_regexes:
        console.print(
            "[dim]Tropes without negative examples still cannot fire without their "
            "activation condition, but a curator has not finished them.[/]"
        )


@platform_group.command(name="status")
def platform_status():
    """Show local cache freshness without contacting the platform."""
    from rich.console import Console

    console = Console()

    async def _status():
        from src.core.database import get_session
        from src.ettok.sync import lexicon_freshness, lexicon_is_usable

        async with get_session() as session:
            count, latest = await lexicon_freshness(session)
            return count, latest, await lexicon_is_usable(session)

    count, latest, usable = asyncio.run(_status())
    console.print(f"Cached lexicon terms: [bold]{count}[/]")
    console.print(f"Last synced: [bold]{latest or 'never'}[/]")
    console.print(
        "[green]✓ fresh enough to scan[/]" if usable else "[red]✗ stale — run platform sync-lexicon[/]"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Knowledge Packs
# ═══════════════════════════════════════════════════════════════════════════

@main.group(name="pack")
def pack_group():
    """Knowledge pack management (taxonomy, lexicon, tropes)"""
    pass


@pack_group.command(name="verify")
@click.argument("pack_dir", type=click.Path(exists=True, path_type=Path), required=False)
@click.option("--strict-gold", is_flag=True, help="Also require the double-annotated gold slice")
def pack_verify(pack_dir: Path | None, strict_gold: bool):
    """Validate a pack without touching the database."""
    from rich.console import Console

    from src.core.settings import get_settings
    from src.packs.verify import verify_pack

    console = Console()
    pack_dir = pack_dir or Path(get_settings().default_pack_dir)
    result = verify_pack(pack_dir, strict_gold=strict_gold)

    for warning in result.warnings:
        console.print(f"[yellow]⚠[/] {warning}")
    for error in result.errors:
        console.print(f"[red]✗[/] {error}")

    if result.ok:
        console.print(f"[green]✓ {pack_dir} is valid[/]")
    sys.exit(0 if result.ok else 1)


@pack_group.command(name="install")
@click.argument("pack_dir", type=click.Path(exists=True, path_type=Path), required=False)
def pack_install(pack_dir: Path | None):
    """Verify and install a knowledge pack into the database."""
    from rich.console import Console

    from src.core.settings import get_settings
    from src.packs.loader import PackError, install_pack

    console = Console()
    pack_dir = pack_dir or Path(get_settings().default_pack_dir)

    async def _install():
        from src.core.database import get_session, init_db

        await init_db()
        async with get_session() as session:
            return await install_pack(session, pack_dir)

    try:
        counts = asyncio.run(_install())
    except PackError as exc:
        console.print(f"[red]✗ {exc}[/]")
        sys.exit(1)

    from src.classifiers.lexicon import LexiconMatcher

    LexiconMatcher.invalidate_cache()

    console.print(f"[green]✓ Installed {pack_dir}[/]")
    for table, count in counts.items():
        console.print(f"  [dim]{table}:[/] {count}")


@pack_group.command(name="list")
def pack_list():
    """Show what is currently installed."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    async def _list():
        from sqlalchemy import func, select

        from src.core.database import get_session
        from src.models.lexicon_entry import LexiconEntry
        from src.models.target_group import TargetGroup
        from src.models.trope_entry import TropeDictionaryEntry

        async with get_session() as session:
            rows = []
            for label, model in (
                ("Target groups", TargetGroup),
                ("Lexicon entries", LexiconEntry),
                ("Tropes", TropeDictionaryEntry),
            ):
                count = (await session.execute(select(func.count(model.id)))).scalar_one()
                source = (
                    await session.execute(
                        select(model.pack_source, model.pack_version).limit(1)
                    )
                ).first()
                rows.append((label, count, source))
            return rows

    table = Table()
    table.add_column("Contents")
    table.add_column("Count", justify="right")
    table.add_column("Pack")
    for label, count, source in asyncio.run(_list()):
        pack = f"{source[0]}@{source[1]}" if source and source[0] else "[dim]—[/]"
        table.add_row(label, str(count), pack)
    console.print(table)


@pack_group.command(name="export")
@click.argument("out_dir", type=click.Path(path_type=Path))
@click.option("--name", default="exported-pack", help="Pack name to write")
@click.option("--pack-version", default="0.1.0", help="Pack version to write")
def pack_export(out_dir: Path, name: str, pack_version: str):
    """Export the current database contents back out as a pack directory."""
    from rich.console import Console

    from src.packs.loader import export_pack

    console = Console()

    async def _export():
        from src.core.database import get_session

        async with get_session() as session:
            return await export_pack(session, out_dir, name=name, version=pack_version)

    path = asyncio.run(_export())
    console.print(f"[green]✓ Exported to {path}[/]")


# ═══════════════════════════════════════════════════════════════════════════
# Cases
# ═══════════════════════════════════════════════════════════════════════════

@main.group(name="cases")
def cases_group():
    """Case management commands"""
    pass


@cases_group.command(name="add")
@click.option("--group", required=True, help="Target group name")
@click.option("--seed", required=True, help="Comma-separated seed URLs")
@click.option("--keywords", required=True, help="Comma-separated keywords")
def cases_add(group: str, seed: str, keywords: str):
    """Register a new incident case."""
    log.info("Adding new case", group=group, seed=seed, keywords=keywords)

    async def _add():
        from src.core.database import get_session
        from src.core.case_manager import CaseManager

        async with get_session() as session:
            manager = CaseManager(session)
            await manager.create_case(
                target_group=group,
                seed_posts=[s.strip() for s in seed.split(",")],
                watch_keywords=[k.strip() for k in keywords.split(",")],
            )

    try:
        asyncio.run(_add())
    except ValueError as exc:
        log.error("Could not add case", error=str(exc))
        sys.exit(1)
    log.info("Case added successfully")


# ═══════════════════════════════════════════════════════════════════════════
# Accounts
# ═══════════════════════════════════════════════════════════════════════════

@main.group(name="accounts")
def accounts_group():
    """Account management commands"""
    pass


@accounts_group.command(name="add")
@click.option("--platform", required=True, help="Platform (facebook, tiktok, instagram)")
@click.option("--username", required=True, help="Account username")
def accounts_add(platform: str, username: str):
    """Add a new worker account for the agent to use."""
    log.info("Adding new worker account", platform=platform, username=username)
    # TODO: Implement account registration
    log.info("Worker account added successfully (stub)")


if __name__ == "__main__":
    main()

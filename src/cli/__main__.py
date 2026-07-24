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
    ankedo update       — Pull latest code + update deps
"""
import asyncio
import os
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


@main.command(name="configure")
@click.argument("section", required=False, default=None)
def configure_cmd(section: str | None):
    """Re-configure a specific section (provider, channels, models).

    Sections: provider, channels, models, all
    """
    from src.cli.setup_wizard import run_setup
    if section == "all" or section is None:
        run_setup(reconfigure=True)
    else:
        # For now, re-run the full wizard
        run_setup(reconfigure=True)


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

    console.print()
    console.print(
        Panel(
            Text.from_markup(
                "[bold cyan]🔺 AnkEdo — Starting Agent[/]\n\n"
                f"[dim]Dashboard:[/] [bold]http://{api_host}:{api_port}[/]\n"
                f"[dim]API Docs:[/]  [bold]http://{api_host}:{api_port}/docs[/]\n"
                "[dim]Press Ctrl+C to stop[/]"
            ),
            border_style="cyan",
            padding=(1, 2),
        )
    )
    console.print()

    # Ensure data directories exist
    for d in ["data", "evidence", "logs", "screenshots"]:
        (PROJECT_ROOT / d).mkdir(exist_ok=True)

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
    if not no_browser:
        import webbrowser
        import threading
        def _open():
            import time
            time.sleep(2)
            webbrowser.open(f"http://{api_host}:{api_port}")
        threading.Thread(target=_open, daemon=True).start()

    # Start uvicorn
    import uvicorn
    uvicorn.run(
        "src.api.main:app",
        host=api_host,
        port=api_port,
        reload=False,
        log_level="info",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Update
# ═══════════════════════════════════════════════════════════════════════════

@main.command(name="update")
@click.option("--skip-deps", is_flag=True, help="Skip dependency update")
def update_cmd(skip_deps: bool):
    """Pull latest code from GitHub, update dependencies, and migrate the database."""
    from rich.console import Console
    console = Console()

    console.print("\n[bold cyan]🔺 AnkEdo — Update[/]\n")

    # Git pull
    console.print("[dim]Pulling latest code...[/]", end=" ")
    try:
        result = subprocess.run(
            ["git", "pull", "origin", "master"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True
        )
        if result.returncode == 0:
            console.print(f"[green]✓[/] {result.stdout.strip()}")
        else:
            console.print(f"[red]✗[/] {result.stderr.strip()}")
            sys.exit(1)
    except FileNotFoundError:
        console.print("[red]✗ Git not found. Install git and try again.[/]")
        sys.exit(1)

    # Update dependencies
    if not skip_deps:
        console.print("[dim]Updating dependencies...[/]", end=" ")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True
        )
        if result.returncode == 0:
            console.print("[green]✓[/]")
        else:
            console.print(f"[yellow]⚠[/] {result.stderr.strip()[:100]}")

    # Re-init database (safe — only creates missing tables)
    console.print("[dim]Checking database schema...[/]", end=" ")
    try:
        from src.core.database import init_db
        asyncio.run(init_db())
        console.print("[green]✓[/]")
    except Exception as e:
        console.print(f"[yellow]⚠ {e}[/]")

    console.print("\n[green bold]✓ Update complete![/]")
    console.print("[dim]Run 'ankedo start' to restart the agent.[/]\n")


# ═══════════════════════════════════════════════════════════════════════════
# Database
# ═══════════════════════════════════════════════════════════════════════════

@main.group(name="db")
def db_group():
    """Database management commands"""
    pass


@db_group.command(name="init")
def db_init():
    """Initialize the SQLite database schema."""
    from rich.console import Console
    console = Console()
    console.print("[dim]Initializing database schema...[/]", end=" ")
    try:
        from src.core.database import init_db
        asyncio.run(init_db())
        console.print("[green]✓ Database initialized successfully.[/]")
    except Exception as e:
        console.print(f"[red]✗ Failed: {e}[/]")
        sys.exit(1)


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
@click.option("--stage", type=str, help="Run only a specific pipeline stage")
def agent_run(continuous: bool, cycles: int, stage: str | None):
    """Start the monitoring agent orchestration loop."""
    log.info("Starting agent run", continuous=continuous, cycles=cycles, stage=stage)
    # TODO: Wire to orchestration_loop.py
    log.info("Agent run complete (stub)")


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
        from src.models.case import CaseSeverity

        async with get_session() as session:
            manager = CaseManager(session)
            await manager.create_case(
                target_group=group,
                seed_posts=[s.strip() for s in seed.split(",")],
                watch_keywords=[k.strip() for k in keywords.split(",")],
                severity=CaseSeverity.MEDIUM,
            )

    asyncio.run(_add())
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

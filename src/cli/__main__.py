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
@click.option("--stage", type=str, help="Run only a specific pipeline stage")
def agent_run(continuous: bool, cycles: int, stage: str | None):
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

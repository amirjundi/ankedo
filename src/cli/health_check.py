"""
Health check / doctor command for AnkEdo.

Usage:
    ankedo doctor           # Run all checks
    ankedo doctor --fix     # Auto-fix what can be fixed
"""
from __future__ import annotations

import asyncio
import importlib
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

console = Console()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = PROJECT_ROOT / ".env"


class Check:
    def __init__(self, name: str, status: str, detail: str, fix: str = ""):
        self.name = name
        self.status = status  # "pass", "warn", "fail"
        self.detail = detail
        self.fix = fix

    @property
    def icon(self) -> str:
        return {"pass": "[green]✓[/]", "warn": "[yellow]⚠[/]", "fail": "[red]✗[/]"}[self.status]


def _check_python() -> Check:
    v = sys.version_info
    version_str = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 11):
        return Check("Python", "pass", f"v{version_str}")
    elif v >= (3, 10):
        return Check("Python", "warn", f"v{version_str} (3.11+ recommended)",
                      "Install Python 3.11+ from python.org")
    else:
        return Check("Python", "fail", f"v{version_str} (3.11+ required)",
                      "Install Python 3.11+ from python.org")


def _check_venv() -> Check:
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        return Check("Virtual Env", "pass", f"Active ({sys.prefix})")
    else:
        return Check("Virtual Env", "warn", "Not in a virtualenv",
                      "Run: python -m venv .venv && .venv\\Scripts\\activate (Windows) or source .venv/bin/activate (Linux)")


def _check_deps() -> Check:
    critical = [
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn"),
        ("sqlalchemy", "sqlalchemy"),
        ("aiosqlite", "aiosqlite"),
        ("click", "click"),
        ("rich", "rich"),
        ("pydantic", "pydantic"),
        ("pydantic_settings", "pydantic-settings"),
        ("httpx", "httpx"),
        ("structlog", "structlog"),
    ]
    missing = []
    for module, package in critical:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)

    if not missing:
        return Check("Dependencies", "pass", f"All {len(critical)} core packages installed")
    else:
        return Check("Dependencies", "fail", f"Missing: {', '.join(missing)}",
                      f"Run: pip install {' '.join(missing)}")


def _check_optional_deps() -> Check:
    optional = [
        ("playwright", "playwright"),
        ("camoufox", "camoufox"),
        ("google.genai", "google-genai"),
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("aiogram", "aiogram"),
    ]
    missing = []
    for module, package in optional:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(package)

    if not missing:
        return Check("Optional Deps", "pass", f"All {len(optional)} optional packages installed")
    elif len(missing) <= 2:
        return Check("Optional Deps", "warn", f"Missing: {', '.join(missing)}",
                      f"Run: pip install {' '.join(missing)}")
    else:
        return Check("Optional Deps", "warn", f"{len(missing)} missing: {', '.join(missing[:3])}...",
                      "Run: pip install -e .")


def _check_env_file() -> Check:
    if ENV_FILE.exists():
        content = ENV_FILE.read_text(encoding="utf-8")
        lines = [l for l in content.splitlines() if l.strip() and not l.strip().startswith("#") and "=" in l]
        return Check(".env File", "pass", f"Found ({len(lines)} config values)")
    else:
        return Check(".env File", "fail", "Not found",
                      "Run: ankedo setup")


def _check_platform() -> Check:
    """Is the Ettok connection configured and reachable?

    Worth a routine check rather than only failing mid-scan: the agent caches the
    lexicon, so it keeps classifying for a while after the platform becomes
    unreachable. The failure is quiet until submissions start piling up.
    """
    if not ENV_FILE.exists():
        return Check("Ettok Platform", "warn", "No .env file", "Run: ankedo setup")

    content = ENV_FILE.read_text(encoding="utf-8")
    values = {}
    for line in content.splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.split("#")[0].strip()

    key = values.get("ETTOK_AGENT_KEY", "")
    base = values.get("ETTOK_BASE_URL", "")
    if not key or not base:
        return Check(
            "Ettok Platform", "warn", "Not connected (agent runs standalone)",
            "Run: ankedo setup",
        )

    try:
        import httpx

        resp = httpx.post(
            base.rstrip("/") + "/heartbeat/",
            headers={
                "Authorization": f"Bearer {key}",
                "X-Agent-Id": values.get("ETTOK_AGENT_ID", "ankedo-agent"),
            },
            json={"agent_id": values.get("ETTOK_AGENT_ID", "ankedo-agent"), "status": "doctor"},
            timeout=10,
        )
    except Exception as exc:
        return Check(
            "Ettok Platform", "fail", f"Unreachable ({type(exc).__name__})",
            "Check network and ETTOK_BASE_URL",
        )

    if resp.status_code == 401:
        return Check("Ettok Platform", "fail", "Key rejected or revoked",
                     "Issue a new key in the Django admin")
    if resp.status_code == 403:
        return Check("Ettok Platform", "fail", "Key lacks the hate_speech_scan scope",
                     "Re-issue with the correct scope")
    if resp.status_code >= 400:
        return Check("Ettok Platform", "fail", f"HTTP {resp.status_code}", "Check the platform")
    return Check("Ettok Platform", "pass", "Connected")


def _check_api_key() -> Check:
    if not ENV_FILE.exists():
        return Check("API Key", "fail", "No .env file", "Run: ankedo setup")

    content = ENV_FILE.read_text(encoding="utf-8")
    providers = {"GEMINI_API_KEY": "Gemini", "OPENAI_API_KEY": "OpenAI", "ANTHROPIC_API_KEY": "Anthropic"}

    found = [
        name
        for var, name in providers.items()
        for line in content.splitlines()
        if line.strip().startswith(f"{var}=") and len(line.split("=", 1)[1].strip()) > 10
    ]

    if found:
        return Check("API Key", "pass", f"{', '.join(found)} key configured")
    return Check("API Key", "fail", "No valid API key found",
                  "Run: ankedo setup  (or set GEMINI_API_KEY in .env)")


def _check_database() -> Check:
    db_path = PROJECT_ROOT / "data" / "ankedo.db"
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        return Check("Database", "pass", f"Found ({size_mb:.1f} MB)")
    else:
        return Check("Database", "warn", "Not initialized yet",
                      "Run: ankedo db init")


def _check_playwright() -> Check:
    try:
        import playwright
        # Check if browsers are installed
        pw_path = Path.home() / "AppData" / "Local" / "ms-playwright"
        if not pw_path.exists():
            pw_path = Path.home() / ".cache" / "ms-playwright"
        if pw_path.exists() and any(pw_path.iterdir()):
            return Check("Browser Engine", "pass", "Playwright browsers installed")
        else:
            return Check("Browser Engine", "warn", "Playwright installed but browsers missing",
                          "Run: playwright install chromium")
    except ImportError:
        return Check("Browser Engine", "warn", "Playwright not installed (needed for collection)",
                      "Run: pip install playwright && playwright install chromium")


def _check_directories() -> Check:
    dirs = ["data", "evidence", "logs"]
    missing = [d for d in dirs if not (PROJECT_ROOT / d).exists()]
    if not missing:
        return Check("Directories", "pass", f"All {len(dirs)} data directories exist")
    else:
        return Check("Directories", "warn", f"Missing: {', '.join(missing)}",
                      "They will be created automatically on first run")


def _check_port() -> Check:
    import socket
    port = 8000
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("API_PORT="):
                try:
                    port = int(line.strip().split("=", 1)[1].strip())
                except ValueError:
                    pass

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.close()
        return Check("Dashboard Port", "pass", f"Port {port} is available")
    except OSError:
        sock.close()
        return Check("Dashboard Port", "warn", f"Port {port} is in use (agent may be running)",
                      f"Stop existing process or change API_PORT in .env")


def _check_git() -> Check:
    if shutil.which("git"):
        return Check("Git", "pass", "Installed")
    else:
        return Check("Git", "warn", "Not found (needed for updates)",
                      "Install from https://git-scm.com")


def _check_node() -> Check:
    if shutil.which("node"):
        return Check("Node.js", "pass", "Installed (for frontend dev)")
    else:
        return Check("Node.js", "warn", "Not found (optional, for frontend development)",
                      "Install from https://nodejs.org")


def run_doctor(fix: bool = False):
    """Run all health checks and display results."""
    console.print()
    console.print(
        Panel(
            Text.from_markup("[bold cyan]🔺 AnkEdo — System Health Check[/]"),
            border_style="cyan",
            padding=(0, 2),
        )
    )
    console.print()

    checks = [
        _check_python(),
        _check_venv(),
        _check_deps(),
        _check_optional_deps(),
        _check_env_file(),
        _check_api_key(),
        _check_platform(),
        _check_database(),
        _check_playwright(),
        _check_directories(),
        _check_port(),
        _check_git(),
        _check_node(),
    ]

    # Auto-fix if requested
    if fix:
        for check in checks:
            if check.status in ("warn", "fail") and check.name == "Directories":
                for d in ["data", "evidence", "logs"]:
                    (PROJECT_ROOT / d).mkdir(exist_ok=True)
                check.status = "pass"
                check.detail = "Created missing directories"

    # Results table
    table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
    table.add_column("Status", width=3, justify="center")
    table.add_column("Component", min_width=16, style="bold")
    table.add_column("Detail")
    table.add_column("Fix", style="dim")

    for c in checks:
        fix_text = c.fix if c.status != "pass" else ""
        table.add_row(c.icon, c.name, c.detail, fix_text)

    console.print(table)

    # Summary
    passed = sum(1 for c in checks if c.status == "pass")
    warned = sum(1 for c in checks if c.status == "warn")
    failed = sum(1 for c in checks if c.status == "fail")
    total = len(checks)

    console.print()
    if failed == 0 and warned == 0:
        console.print(f"[green bold]All {total} checks passed! ✓[/]")
        console.print("[dim]Run 'ankedo start' to launch the agent.[/]")
    elif failed == 0:
        console.print(f"[green]{passed}/{total} passed[/], [yellow]{warned} warnings[/]")
        console.print("[dim]Warnings are non-blocking — the agent can still run.[/]")
    else:
        console.print(f"[green]{passed}/{total} passed[/], [yellow]{warned} warnings[/], [red]{failed} failures[/]")
        console.print("[dim]Fix failures before running the agent. Use 'ankedo setup' for configuration.[/]")

    console.print()
    return failed == 0

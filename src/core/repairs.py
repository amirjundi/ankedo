"""Repairs the agent may perform on its own installation.

Every `Check` in the doctor already knows the command that would fix it — `Check.fix`
has held that string all along, and nothing ever ran it. This is the registry that
does, and the boundary around it.

The shape is deliberately the same as `src/core/self_tuner.py` (SELF_TUNABLE versus
PROPOSAL_ONLY) and `src/chat/tools.py` (a fixed ACTIONS dict), because the risk is the
same and the answer that worked there works here:

**The model never composes a command.** It names a repair; Python looks the name up and
runs a hardcoded argv. There is no string interpolation into a shell, no `shell=True`,
and no path by which an unregistered command can execute. A prompt injection in a
comment being classified can, at worst, name a repair that already exists.

**Anything needing root is proposal-only.** `sudo apt install` is not something an agent
should attempt unattended on a machine holding evidence about persecuted minorities. It
becomes a notification with the exact command, for a human. This mirrors
`ResilientCollector._propose_selectors`, which refuses to auto-apply what a model
invented, and `SelfTuner.propose`.

This module introduces runtime command execution to a codebase that had none outside
the interactive `ankedo update` CLI. That is the reason for the narrow surface.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# A repair that hangs is worse than one that fails: the loop that called it is waiting.
DEFAULT_TIMEOUT = 300


@dataclass(frozen=True)
class Repair:
    name: str
    description: str
    # The Check.name this repairs, so `doctor --fix` can match them up.
    applies_to: str
    # Fixed argv lists, run in order until one succeeds. Never built from input.
    commands: tuple[tuple[str, ...], ...] = ()
    # True when a human must run it — needs root, or needs a decision.
    needs_human: bool = False
    # The command to show a human for a proposal-only repair.
    manual: str = ""
    timeout: int = DEFAULT_TIMEOUT


@dataclass
class RepairResult:
    name: str
    ok: bool
    detail: str
    proposed: bool = False
    log: list[str] = field(default_factory=list)


class RepairError(Exception):
    """The repair could not be attempted. The message is shown to the operator."""


REPAIRS: dict[str, Repair] = {
    r.name: r
    for r in [
        Repair(
            name="browser",
            description="Install the browser the collector launches",
            applies_to="Browser Engine",
            # Camoufox first, because that is what CamoufoxWorker actually starts — it
            # is a Firefox fork with its own download, not a Playwright browser. An
            # earlier version of this installed chromium, which succeeded and left the
            # check failing on a missing firefox: the repair fixed a browser nothing
            # launches.
            #
            # Then Playwright's firefox as a second source, then an upgrade and retry
            # for a Playwright too old to know this distro. The final fallback — using
            # a browser already on the machine — writes config rather than running a
            # command, so it lives in _adopt_system_browser.
            commands=(
                (sys.executable, "-m", "camoufox", "fetch"),
                (sys.executable, "-m", "playwright", "install", "firefox"),
                (sys.executable, "-m", "pip", "install", "-U", "camoufox", "playwright"),
                (sys.executable, "-m", "camoufox", "fetch"),
            ),
        ),
        Repair(
            name="dependencies",
            description="Reinstall Python dependencies",
            applies_to="Dependencies",
            commands=((sys.executable, "-m", "pip", "install", "-e", ".", "--quiet"),),
        ),
        Repair(
            name="directories",
            description="Create the missing data directories",
            applies_to="Directories",
        ),
        Repair(
            name="env_file",
            description="Create .env from .env.example",
            applies_to=".env File",
        ),
        Repair(
            name="system_browser",
            description="Install a system browser (needs root)",
            applies_to="Browser Engine",
            needs_human=True,
            manual="sudo apt install chromium-browser",
        ),
    ]
}


async def _run(argv: tuple[str, ...], timeout: int) -> tuple[bool, str]:
    """Run one fixed argv. No shell, so nothing is word-split or expanded."""
    log.info("Running repair command", argv=list(argv))
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        return False, f"{argv[0]} not found"

    try:
        out, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        return False, f"timed out after {timeout}s"

    text = (out or b"").decode("utf-8", "replace").strip()
    tail = text.splitlines()[-1][:200] if text else ""
    return process.returncode == 0, tail


# ── Repairs that write files rather than running commands ────────────────────


def _make_directories() -> tuple[bool, str]:
    made = []
    for name in ("data", "evidence", "logs", "screenshots"):
        path = PROJECT_ROOT / name
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            made.append(name)
    return True, f"created {', '.join(made)}" if made else "nothing was missing"


def _make_env_file() -> tuple[bool, str]:
    target = PROJECT_ROOT / ".env"
    if target.exists():
        return True, ".env already exists"
    example = PROJECT_ROOT / ".env.example"
    if not example.exists():
        return False, "no .env.example to copy"
    # Through the wizard's writer, so placeholder values are stripped and a SECRET_KEY
    # is generated rather than left blank.
    from src.cli.setup_wizard import _write_env
    import secrets

    _write_env({"SECRET_KEY": secrets.token_hex(32)})
    return True, "created .env — run `ankedo setup` to add an API key"


def _adopt_system_browser() -> tuple[bool, str]:
    """Point the agent at a browser already installed, instead of asking for root.

    The last resort for a distro Playwright has no build for. Writing the path is
    something the agent may do; installing a package system-wide is not.
    """
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found:
            from src.cli.setup_wizard import _load_existing_env, _write_env
            from src.core.settings import get_settings

            config = _load_existing_env()
            if not config:
                return False, f"found {found} but there is no .env to record it in"
            config["BROWSER_EXECUTABLE_PATH"] = found
            _write_env(config)
            get_settings.cache_clear()
            return True, f"using the system browser at {found}"
    return False, "no system browser found"


async def run_repair(name: str) -> RepairResult:
    """Perform one named repair.

    Raises RepairError for an unknown name — an unregistered repair is not a thing that
    can happen, however the request was phrased.
    """
    repair = REPAIRS.get(name)
    if repair is None:
        raise RepairError(f"No such repair: {name}. Known: {', '.join(REPAIRS)}")

    if repair.needs_human:
        return RepairResult(
            name=name,
            ok=False,
            proposed=True,
            detail=f"needs a human: {repair.manual}",
        )

    if name == "directories":
        ok, detail = _make_directories()
        return RepairResult(name, ok, detail)

    if name == "env_file":
        ok, detail = _make_env_file()
        return RepairResult(name, ok, detail)

    trail: list[str] = []
    for argv in repair.commands:
        ok, detail = await _run(argv, repair.timeout)
        trail.append(f"{argv[-1]}: {'ok' if ok else detail}")
        if ok and argv[1:3] != ("-m", "pip"):
            # A successful pip upgrade is a step, not the goal — keep going so the
            # retry that actually installs the browser runs.
            return RepairResult(name, True, detail or "done", log=trail)

    if name == "browser":
        # Every install path failed. Before giving up, look for a browser already here.
        ok, detail = _adopt_system_browser()
        trail.append(f"system browser: {detail}")
        if ok:
            return RepairResult(name, True, detail, log=trail)
        return RepairResult(
            name, False,
            f"{detail}. Install one manually: {REPAIRS['system_browser'].manual}",
            log=trail,
        )

    return RepairResult(name, False, trail[-1] if trail else "nothing to do", log=trail)


def repairs_for(check_name: str) -> list[Repair]:
    """The repairs that address a given check, automatic ones first."""
    matches = [r for r in REPAIRS.values() if r.applies_to == check_name]
    return sorted(matches, key=lambda r: r.needs_human)


def catalogue() -> str:
    """The repair list, for a prompt or a help message."""
    lines = []
    for repair in REPAIRS.values():
        mark = " [needs a human]" if repair.needs_human else ""
        lines.append(f"- {repair.name}{mark}: {repair.description}")
    return "\n".join(lines)

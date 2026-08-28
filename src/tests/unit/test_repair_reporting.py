"""The agent must be able to check itself, and say something true when a repair fails.

From the operator's transcript, in order:

    can you use the browser ?
    → Run the 'browser' repair, which may install software. Confirm?
    → Repaired browser: Version 'official' not found in cache. Run 'camoufox sync'.

    you can run it, run anything just fix the problem
    → Could not repair dependencies: --quiet: ok

    how to fix it ?
    → asyncio.run() being called from a running event loop ...
      I cannot run these commands from this chat.

Three faults. The browser repair ran `camoufox fetch`, reported success, and left the
browser missing — the subcommand had been renamed to `sync`, so the repair did nothing
and said it worked. The dependencies failure was explained as "--quiet: ok", a
fragment of an unrelated line offered as the reason. And the health check called
asyncio.run() from inside the API's event loop, so asking the agent to diagnose itself
crashed the diagnosis.

Together they made an agent that could act look like one that could not — it kept
telling the operator to go and run things themselves.
"""
from __future__ import annotations

import asyncio

import pytest

from src.core.repairs import REPAIRS, _explain


def _browser_commands():
    """REPAIRS maps a name to one Repair, not to a list of them."""
    repair = REPAIRS.get("browser")
    assert repair is not None, "no browser repair registered"
    return [" ".join(c[1:]) for c in repair.commands]


# ── the renamed subcommand ───────────────────────────────────────────────────


def test_the_browser_repair_tries_sync():
    """Camoufox renamed fetch to sync. The machine said so, in the failure message,
    and the agent could not act on its own instruction."""
    assert any("camoufox sync" in c for c in _browser_commands())


def test_the_browser_repair_still_tries_fetch():
    """Which subcommand exists depends on the installed version, so both are tried
    and the unknown one simply fails and moves on."""
    assert any("camoufox fetch" in c for c in _browser_commands())


# ── explaining a failure ─────────────────────────────────────────────────────


def test_a_failure_with_no_output_says_so():
    assert "no output" in _explain("", ok=False)


def test_an_error_line_is_preferred_over_the_last_line():
    """The bug exactly: the last line was "--quiet: ok" and the real cause was
    further up. A wrong explanation sends someone to debug something that is not
    happening."""
    output = "\n".join([
        "Collecting camoufox",
        "ERROR: Could not find a version that satisfies the requirement camoufox",
        "  --quiet: ok",
    ])

    explained = _explain(output, ok=False)

    assert "Could not find a version" in explained


def test_context_is_kept_not_just_one_line():
    output = "\n".join([
        "error: subprocess-exited-with-error",
        "error: metadata-generation-failed",
        "note: this is an issue with the package",
    ])

    explained = _explain(output, ok=False)

    assert explained.count("/") >= 1, "only one line survived"


def test_success_reports_the_last_line():
    assert _explain("Downloading\nDone.", ok=True) == "Done."


def test_an_explanation_is_bounded():
    """A repair that prints a megabyte of build output must not paste it into chat."""
    assert len(_explain("error: x" * 500, ok=False)) <= 400


# ── the agent can diagnose itself ────────────────────────────────────────────


def test_health_checks_run_inside_a_running_event_loop():
    """The chat `health` action runs inside the API's loop. asyncio.run() raises
    there, so asking the agent whether it was healthy crashed the check — and the
    operator was told the browser was broken for a reason that was really this."""
    from src.cli.health_check import run_checks

    async def from_within_a_loop():
        return run_checks()

    checks = asyncio.run(from_within_a_loop())

    assert checks, "no checks ran"
    assert any(c.name == "Browser Engine" for c in checks)


def test_health_checks_still_run_without_a_loop():
    """The CLI path, which has no loop at all."""
    from src.cli.health_check import run_checks

    assert run_checks()

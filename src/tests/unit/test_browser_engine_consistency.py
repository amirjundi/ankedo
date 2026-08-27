"""Everything that installs, repairs or documents a browser must mean the same one.

The agent launches `AsyncCamoufox` — a hardened Firefox fork, chosen because
Playwright-driven Chromium is trivially fingerprinted and a fingerprinted worker
account is a banned one. Three other places disagreed with it:

* the installer ran `playwright install chromium`, which on the operator's machine
  failed outright with "does not support chromium on ubuntu26.04-x64" — and would
  have been useless even had it succeeded, since nothing launches Chromium
* the repair registry offered `sudo apt install chromium-browser`
* `_adopt_system_browser` searched for chromium/chrome binaries and wrote whichever it
  found into BROWSER_EXECUTABLE_PATH, where Camoufox would try to start it as Firefox

Each looked reasonable alone. Together they meant a successful install, a successful
repair, and a browser that could not launch — with the failure pointing at the browser
rather than at the three places that had installed the wrong one.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "install.sh"
REPAIRS = ROOT / "src" / "core" / "repairs.py"
WORKER = ROOT / "src" / "browsers" / "camoufox_worker.py"


def _code_only(text: str) -> str:
    """Strip comment lines, so an explanation of the old bug is not read as the bug."""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        lines.append(line.split("  #")[0])
    return "\n".join(lines)


def test_the_agent_launches_camoufox():
    """The premise everything else has to agree with."""
    assert "AsyncCamoufox" in WORKER.read_text(encoding="utf-8")


def test_the_installer_fetches_camoufox():
    code = _code_only(INSTALLER.read_text(encoding="utf-8"))

    assert "camoufox fetch" in code


def test_the_installer_does_not_install_chromium():
    code = _code_only(INSTALLER.read_text(encoding="utf-8"))

    assert "playwright install chromium" not in code, (
        "installs a browser the agent never launches, and fails on distros "
        "Playwright's build registry does not know"
    )


def test_the_repair_registry_fetches_camoufox():
    code = _code_only(REPAIRS.read_text(encoding="utf-8"))

    assert '"camoufox", "fetch"' in code or "camoufox" in code


def test_no_repair_offers_to_install_chromium():
    code = _code_only(REPAIRS.read_text(encoding="utf-8"))

    assert "chromium-browser" not in code
    assert "google-chrome" not in code


def test_the_system_browser_search_looks_for_firefox():
    """BROWSER_EXECUTABLE_PATH is handed to Camoufox, which is Firefox. A Chromium
    path there fails at collection time, long after the repair claimed success."""
    code = _code_only(REPAIRS.read_text(encoding="utf-8"))

    # Scoped to the function: repairs.py has other `for name in (...)` loops, and the
    # first one is a list of data directories. A test that asserts against the wrong
    # loop passes or fails for reasons unrelated to what it claims to check.
    start = code.index("def _adopt_system_browser")
    body = code[start:start + 1200]

    match = re.search(r'for name in \(([^)]*)\)', body)
    assert match, "could not find the browser search list"

    names = match.group(1)
    assert "firefox" in names
    assert "chromium" not in names and "chrome" not in names


def test_the_installer_still_says_the_agent_works_without_a_browser():
    """Classification and the dashboard do not need one, and an operator who cannot
    get a browser up should not conclude the install failed."""
    text = INSTALLER.read_text(encoding="utf-8")

    assert "work without it" in text

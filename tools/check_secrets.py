"""Block credentials and collected content from reaching a public repo.

Two distinct risks, both realistic and neither about someone reading the source:

* **A pasted key.** An operator or developer drops a real token into a config file,
  a test fixture, or a README while debugging. `.env` is gitignored; a key that lands
  anywhere else is not.
* **Collected content.** Screenshots, the database, and evidence files hold material
  about people who are already targets of violence. Committing one puts it in git
  history permanently, where deleting the file does not remove it.

Runs as a pre-commit hook, and standalone:

    python tools/check_secrets.py [files...]
    python tools/check_secrets.py --paths-only [files...]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Patterns for real credentials, not placeholders. Each requires enough entropy or
# structure that an example value will not match.
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("OpenAI key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # An assignment with a long, high-entropy value — catches the agent key and
    # anything else pasted into a settings file.
    (
        "assigned secret",
        # No \b before the name: ETTOK_AGENT_KEY has a word character before "AGENT",
        # so a leading boundary would never match the very variables we care about.
        re.compile(
            r"(?i)(api[_-]?key|secret[_-]?key|agent[_-]?key|access[_-]?token|token|password|passwd)"
            r"\s*[:=]\s*[\"']([A-Za-z0-9_\-]{24,})[\"']"
        ),
    ),
]

# Obvious non-secrets that would otherwise trip the assignment pattern.
PLACEHOLDERS = re.compile(
    r"(?i)(your[_-]?|example|placeholder|xxx+|<.*>|changeme|dummy|fake|test[_-]?key"
    r"|\.\.\.|generate|token_hex|redacted|\*{4,})"
)

# Paths that must never be committed, regardless of content.
FORBIDDEN_PATHS = re.compile(
    r"(^|/)("
    r"\.env$"
    r"|data/.*\.db$"
    r"|evidence/"
    r"|screenshots/"
    r"|sessions/"
    r"|backups/"
    r")"
)

TEXT_SUFFIXES = {
    ".py", ".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".txt", ".md",
    ".sh", ".ps1", ".js", ".jsx", ".ts", ".tsx", ".html", ".env",
}


def check_path(path: Path) -> list[str]:
    posix = path.as_posix()
    if FORBIDDEN_PATHS.search(posix):
        return [
            f"{posix}: this path holds credentials or collected content about real "
            "people — it must never be committed"
        ]
    return []


def _looks_like_words(value: str) -> bool:
    """True for readable placeholders like 'test-admin-token-that-is-long-enough'.

    A real credential is high-entropy; a value made only of alphabetic, hyphenated
    segments is something a human typed. Cheaper and more reliable than maintaining
    an ever-growing list of placeholder keywords.
    """
    segments = re.split(r"[-_]", value)
    return len(segments) >= 3 and all(s.isalpha() for s in segments if s)


def check_content(path: Path) -> list[str]:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    problems = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if PLACEHOLDERS.search(line):
            continue
        for label, pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if match:
                captured = match.groups()[-1] if match.groups() else match.group(0)
                if _looks_like_words(captured):
                    continue
                problems.append(
                    f"{path.as_posix()}:{lineno}: looks like a {label}. "
                    "Move it to .env, and rotate it — anything committed is "
                    "permanently in git history."
                )
                break
    return problems


def main(argv: list[str]) -> int:
    paths_only = "--paths-only" in argv
    files = [Path(a) for a in argv if not a.startswith("--")]

    if not files:  # standalone: scan what git tracks
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True, check=False
        ).stdout.splitlines()
        files = [Path(f) for f in tracked]

    problems: list[str] = []
    for path in files:
        if not path.exists():
            continue
        problems.extend(check_path(path))
        if not paths_only:
            problems.extend(check_content(path))

    for problem in problems:
        print(f"BLOCKED  {problem}")

    if problems:
        print(f"\n{len(problems)} problem(s). Nothing committed.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

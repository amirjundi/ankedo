"""Every console.print literal must be valid rich markup.

Rich parses each print call independently, so a tag opened in one call and closed
in the next raises MarkupError at runtime — and these strings only execute when an
operator reaches that step of the wizard, which is how a broken menu shipped and
was found on the target machine rather than here.

This walks the source instead of running it, so an unbalanced tag fails in CI on
the line that has it.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from rich.errors import MarkupError
from rich.markup import render

SRC = Path(__file__).resolve().parents[2]
FILES = sorted((SRC / "cli").glob("*.py")) + sorted((SRC / "chat").rglob("*.py"))


def _print_literals(path: Path):
    """(lineno, string) for every plain literal passed to console.print."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "print"):
            continue
        if not (isinstance(func.value, ast.Name) and func.value.id == "console"):
            continue
        for arg in node.args:
            # Implicitly concatenated literals fold into one Constant; f-strings and
            # runtime expressions are skipped, since their markup cannot be checked
            # without the values.
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                yield arg.lineno, arg.value


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_console_markup_is_balanced(path):
    failures = []
    for lineno, text in _print_literals(path):
        try:
            render(text)
        except MarkupError as exc:
            failures.append(f"{path.name}:{lineno}: {exc}\n    {text!r}")

    assert not failures, "invalid rich markup:\n" + "\n".join(failures)


def test_the_check_catches_a_tag_split_across_calls():
    """Guard the guard: the real bug was a [/] with nothing to close."""
    with pytest.raises(MarkupError):
        render("                        safety filters can be turned off[/]")

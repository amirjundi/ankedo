"""Every module under src/ must import.

Cheapest possible guard: two modules shipped with SyntaxErrors for weeks because
nothing imported them. This catches that class of breakage in one test.
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest

import src

MODULES = sorted(
    m.name
    for m in pkgutil.walk_packages(src.__path__, prefix="src.")
    if not m.name.startswith("src.tests.")
)


def test_modules_were_discovered():
    # a broken walk would make the parametrized test vacuously pass
    assert len(MODULES) > 50, f"only found {len(MODULES)} modules"


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name: str):
    importlib.import_module(module_name)

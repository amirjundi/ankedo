"""Migrations must stay in step with the models.

Tests build their schema with `create_all` because it is fast, while production runs
Alembic. That gap is a trap: change a model without generating a migration and every
test still passes, then the VPS breaks on deploy — or worse, `create_all` quietly
skips the new column and the failure surfaces as missing data.

This test closes it by asking Alembic whether the migrated schema still differs from
the models. A non-empty diff means someone forgot:

    alembic revision --autogenerate -m "<what changed>"
"""
from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from src.core.database import Base

ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_migrations_apply_to_an_empty_database(tmp_path, monkeypatch):
    db = tmp_path / "migrate.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    from src.core.settings import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(f"sqlite+aiosqlite:///{db}"), "head")
        assert db.exists()
    finally:
        get_settings.cache_clear()


def test_no_model_changes_are_missing_a_migration(tmp_path, monkeypatch):
    db = tmp_path / "drift.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    from src.core.settings import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(f"sqlite+aiosqlite:///{db}"), "head")

        engine = create_engine(f"sqlite:///{db}")
        with engine.connect() as conn:
            context = MigrationContext.configure(
                conn, opts={"compare_type": True, "render_as_batch": True}
            )
            diff = compare_metadata(context, Base.metadata)
        engine.dispose()
    finally:
        get_settings.cache_clear()

    # Alembic reports SQLite indexes it cannot introspect as spurious removals;
    # only real structural drift matters here.
    real = [d for d in diff if _is_structural(d)]
    assert not real, (
        "models differ from migrations — run:\n"
        '  alembic revision --autogenerate -m "<what changed>"\n\n'
        f"{real}"
    )


def _is_structural(diff) -> bool:
    op = diff[0] if isinstance(diff, tuple) else diff
    if isinstance(op, list):  # grouped column diffs
        return any(_is_structural(d) for d in op)
    return str(op) in {
        "add_table",
        "remove_table",
        "add_column",
        "remove_column",
        "modify_type",
        "modify_nullable",
    }

"""A trope with no activation data must never fire on its own.

Ettok ships the §4 schema with content still empty: the 19 seeded tropes carry
`activation_topics: []` and `negative_examples: []` until a curator backfills them
from the Duhok transcript.

Reading that as "no gate, so always active" would flag every ordinary devout comment
on the platform — the precise failure this design exists to prevent, and worse than
missing hate because it silences the community being protected. The safe reading is
"no deterministic gate yet": the surface form still surfaces as a candidate that
raises review priority, and a human decides.
"""
from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from src.core.settings import get_settings

PACK_DIR = Path(__file__).resolve().parents[2] / "packs" / "iraq-minorities"
PIOUS = "اعوذ بالله من الشيطان الرجيم"


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import get_session, init_db
    from src.packs.loader import install_pack

    await init_db()
    async with get_session() as s:
        await install_pack(s, PACK_DIR)
        yield s

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


async def _strip_activation(session):
    """Reduce the seeded trope to what an unbackfilled Ettok trope looks like."""
    from sqlalchemy import select

    from src.models.trope_entry import TropeDictionaryEntry

    trope = (await session.execute(select(TropeDictionaryEntry))).scalars().one()
    trope.activation = {}          # no requires_target_group, no topics
    trope.negative_examples = []
    await session.commit()
    return trope


async def test_unbackfilled_trope_does_not_fire_without_group_context(session):
    from src.classifiers.trope_engine import TropeEngine

    await _strip_activation(session)

    result = await TropeEngine(session).evaluate(
        {
            "comment_text": PIOUS,
            "parent_post_text": "شاهدوا هذا الثعبان الضخم في الحديقة",
            "target_groups": [],
        }
    )

    assert result["fired"] == [], "an empty activation block must not mean 'always active'"
    assert [t["trope_id"] for t in result["candidates"]] == ["yazidi-devil-worship"]


async def test_unbackfilled_trope_still_fires_with_group_context(session):
    """The group link survives the backfill gap — only the topic list is missing."""
    from src.classifiers.trope_engine import TropeEngine

    await _strip_activation(session)

    result = await TropeEngine(session).evaluate(
        {
            "comment_text": PIOUS,
            "parent_post_text": "مراسم دينية إيزيدية في معبد لالش",
            "target_groups": ["yazidi"],
        }
    )

    assert [t["trope_id"] for t in result["fired"]] == ["yazidi-devil-worship"]


async def test_missing_requires_flag_defaults_to_requiring_a_group(session):
    """Absent means strict. The permissive default is the one that over-flags."""
    from src.classifiers.trope_engine import TropeEngine

    from sqlalchemy import select

    from src.models.trope_entry import TropeDictionaryEntry

    trope = (await session.execute(select(TropeDictionaryEntry))).scalars().one()
    trope.activation = {"negation_cancels": True}  # requires_target_group absent
    await session.commit()

    result = await TropeEngine(session).evaluate(
        {"comment_text": PIOUS, "parent_post_text": "طقس جميل اليوم", "target_groups": []}
    )

    assert result["fired"] == []

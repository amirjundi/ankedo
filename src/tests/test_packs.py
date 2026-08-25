"""Knowledge pack install, group resolution, and trope activation.

The trope tests are the ones that matter: they encode SRS §4.4.0's worked example,
where the same pious phrase must be hate on Yazidi-related content and benign
everywhere else. If these ever pass in only one direction the classifier is broken
in the specific way that makes it unusable.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from src.core.settings import get_settings

PACK_DIR = Path(__file__).resolve().parents[2] / "packs" / "iraq-minorities"


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import get_session, init_db

    await init_db()
    async with get_session() as s:
        yield s

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


@pytest_asyncio.fixture
async def installed(session):
    from src.packs.loader import install_pack

    await install_pack(session, PACK_DIR)
    return session


# --------------------------------------------------------------------------- verify


def test_seed_pack_verifies():
    from src.packs.verify import verify_pack

    result = verify_pack(PACK_DIR)
    assert result.ok, result.errors


def test_pattern_tropes_need_no_surface_forms(tmp_path):
    """About 40% of the field data describes a pattern rather than quoting one.

    "Discrimination over holiday leave" is a real documented trope that nobody types
    verbatim. Requiring a literal string would reject the analysts' best material.
    """
    import shutil

    import yaml

    from src.packs.verify import verify_pack

    shutil.copytree(PACK_DIR, tmp_path / "pack")
    tropes_file = tmp_path / "pack" / "tropes.yaml"
    doc = yaml.safe_load(tropes_file.read_text(encoding="utf-8"))
    doc["entries"].append({
        "trope_id": "collective-blame",
        "target_group": "yazidi",
        "surface_forms": [],  # nothing literal to match
        "implicature": "Treating one member's act as proof of a community trait.",
        "activation": {"requires_target_group": True},
        "negative_examples": [{"comment_text": "this person behaved badly"}],
        "source": "duhok-focus-group row 10",
    })
    tropes_file.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    assert verify_pack(tmp_path / "pack").ok


def test_a_trope_with_nothing_to_recognise_is_rejected(tmp_path):
    """No surface forms AND no description means the row does nothing."""
    import shutil

    import yaml

    from src.packs.verify import verify_pack

    shutil.copytree(PACK_DIR, tmp_path / "pack")
    tropes_file = tmp_path / "pack" / "tropes.yaml"
    doc = yaml.safe_load(tropes_file.read_text(encoding="utf-8"))
    doc["entries"].append({
        "trope_id": "empty",
        "target_group": "yazidi",
        "surface_forms": [],
        "negative_examples": [{"comment_text": "x"}],
        "source": "test",
    })
    tropes_file.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    result = verify_pack(tmp_path / "pack")
    assert not result.ok
    assert any("recognise" in e for e in result.errors)


def test_trope_without_negative_examples_is_rejected(tmp_path):
    """The rule that stops the system flagging all devout speech."""
    import shutil

    import yaml

    from src.packs.verify import verify_pack

    shutil.copytree(PACK_DIR, tmp_path / "pack")
    tropes_file = tmp_path / "pack" / "tropes.yaml"
    doc = yaml.safe_load(tropes_file.read_text(encoding="utf-8"))
    doc["entries"][0]["negative_examples"] = []
    tropes_file.write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")

    result = verify_pack(tmp_path / "pack")
    assert not result.ok
    assert any("negative_examples" in e for e in result.errors)


# --------------------------------------------------------------------------- install


async def test_install_loads_pack(installed):
    from sqlalchemy import func, select

    from src.models.target_group import TargetGroup
    from src.models.trope_entry import TropeDictionaryEntry

    groups = (await installed.execute(select(func.count(TargetGroup.id)))).scalar_one()
    tropes = (await installed.execute(select(func.count(TropeDictionaryEntry.id)))).scalar_one()
    assert groups == 8
    assert tropes == 1


async def test_install_is_idempotent(installed):
    from sqlalchemy import func, select

    from src.models.target_group import TargetGroup
    from src.packs.loader import install_pack

    await install_pack(installed, PACK_DIR)
    groups = (await installed.execute(select(func.count(TargetGroup.id)))).scalar_one()
    assert groups == 8, "re-installing a pack must upgrade rows, not duplicate them"


# --------------------------------------------------------------------- group resolver


@pytest.mark.parametrize(
    "text,expected",
    [
        ("مراسم دينية إيزيدية في معبد لالش", "yazidi"),
        ("الايزيديين في سنجار", "yazidi"),          # no hamza
        ("Yezidi community news", "yazidi"),        # transliteration
        ("ئێزیدی", "yazidi"),                        # Sorani script
        ("شاهدوا هذا الثعبان الضخم في الحديقة", None),
    ],
)
async def test_group_resolution(installed, text, expected):
    from src.classifiers.group_resolver import GroupResolver

    assert await GroupResolver(installed).resolve(text) == expected


async def test_longest_alias_wins(installed):
    """A specific group must not lose to a broader one sharing a substring."""
    from src.classifiers.group_resolver import GroupResolver

    assert await GroupResolver(installed).resolve("الكرد الفيليون") == "faili-kurd"


# ---------------------------------------------------------------- trope activation
# SRS §4.4.0: identical text, opposite verdicts, decided only by context.

PIOUS_PHRASE = "اعوذ بالله من الشيطان الرجيم"


async def test_trope_fires_on_target_group_content(installed):
    from src.classifiers.trope_engine import TropeEngine

    result = await TropeEngine(installed).evaluate(
        {
            "comment_text": PIOUS_PHRASE,
            "parent_post_text": "مراسم دينية إيزيدية في معبد لالش",
            "target_groups": ["yazidi"],
        }
    )
    assert [t["trope_id"] for t in result["fired"]] == ["yazidi-devil-worship"]


async def test_trope_does_not_fire_without_target_group(installed):
    """The failure mode that would make the system unusable: flagging devout speech."""
    from src.classifiers.trope_engine import TropeEngine

    result = await TropeEngine(installed).evaluate(
        {
            "comment_text": PIOUS_PHRASE,
            "parent_post_text": "شاهدوا هذا الثعبان الضخم في الحديقة",
            "target_groups": [],
        }
    )
    assert result["fired"] == []
    # still surfaced as a candidate so it can raise review priority
    assert [t["trope_id"] for t in result["candidates"]] == ["yazidi-devil-worship"]


async def test_counter_speech_does_not_fire(installed):
    """Flagging people defending the group is the highest-cost false positive."""
    from src.classifiers.trope_engine import TropeEngine

    result = await TropeEngine(installed).evaluate(
        {
            "comment_text": "الإيزيديون ليسوا عبدة الشيطان، هذا افتراء",
            "parent_post_text": "مراسم دينية إيزيدية في معبد لالش",
            "target_groups": ["yazidi"],
        }
    )
    assert result["fired"] == []


# --------------------------------------------------------------------------- export


async def test_export_round_trips(installed, tmp_path):
    from src.packs.loader import export_pack
    from src.packs.verify import verify_pack

    out = await export_pack(installed, tmp_path / "out", name="iraq-minorities", version="0.1.0")
    assert verify_pack(out).ok

    import yaml

    original = yaml.safe_load((PACK_DIR / "target_groups.yaml").read_text(encoding="utf-8"))
    exported = yaml.safe_load((out / "target_groups.yaml").read_text(encoding="utf-8"))
    assert {g["slug"] for g in original} == {g["slug"] for g in exported}

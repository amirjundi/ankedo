"""Install and export knowledge packs.

Install is idempotent and keyed on natural identifiers (group slug, trope id,
term+group) so re-running an install upgrades rows in place rather than duplicating
them. Every row records `pack_source` and `pack_version`, which is what lets a
classification record exactly which dictionary versions produced it (FR-CL-14).
"""
from __future__ import annotations

import json
from pathlib import Path

import structlog
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.lexicon_entry import LexiconEntry, TermScope
from src.models.severity_level import SeverityLevel
from src.models.target_group import TargetGroup
from src.models.trope_entry import TropeDictionaryEntry
from src.packs.verify import VerifyResult, _entries, _load_yaml, verify_pack

log = structlog.get_logger()

# Default scale, used only when a pack ships none. The real levels are a domain
# decision (SRS §7 Q2) — this exists so nothing blocks on it.
DEFAULT_SEVERITY_LEVELS = [
    {"ordinal": 1, "label": "Low", "auto_escalate": False},
    {"ordinal": 2, "label": "Medium", "auto_escalate": False},
    {"ordinal": 3, "label": "High", "auto_escalate": True},
    {"ordinal": 4, "label": "Severe", "auto_escalate": True},
]


class PackError(RuntimeError):
    """Raised when a pack cannot be installed."""


def _resolve_scope(entry: dict, slug_to_id: dict, group_rows: dict) -> tuple[TermScope, list]:
    """Read an entry's group scope.

    Accepts `scope: universal`, or `target_groups: [a, b]`, or legacy single
    `target_group: a`. Universal must be stated explicitly — an empty group list is
    never silently promoted to "applies to everyone", because that is exactly the
    mistake that makes the system flag ordinary speech.
    """
    if str(entry.get("scope", "")).lower() == "universal":
        return TermScope.UNIVERSAL, []

    slugs = entry.get("target_groups")
    if slugs is None:
        single = entry.get("target_group")
        slugs = [single] if single else []

    return TermScope.GROUP_SPECIFIC, [group_rows[s] for s in slugs]


async def install_pack(session: AsyncSession, pack_dir: Path) -> dict[str, int]:
    """Verify then install a pack. Returns per-table upsert counts."""
    result: VerifyResult = verify_pack(pack_dir)
    if not result.ok:
        raise PackError(
            f"pack failed verification:\n  " + "\n  ".join(result.errors)
        )

    meta = _load_yaml(pack_dir / "pack.yaml") or {}
    name, version = meta["name"], str(meta["version"])
    counts = {"target_groups": 0, "severity_levels": 0, "lexicon": 0, "tropes": 0}

    # --- target groups (must land first: everything else references them) -----
    slug_to_id: dict[str, str] = {}
    group_rows: dict[str, TargetGroup] = {}
    for entry in _entries(_load_yaml(pack_dir / "target_groups.yaml")):
        slug = entry["slug"]
        names = entry.get("display_name") or {}
        group = (
            await session.execute(select(TargetGroup).where(TargetGroup.slug == slug))
        ).scalar_one_or_none()
        if group is None:
            group = TargetGroup(slug=slug)
            session.add(group)
        group.display_name_en = names.get("en") or slug
        group.display_name_ar = names.get("ar")
        group.display_name_ku = names.get("ku")
        group.aliases = entry.get("aliases") or []
        group.self_reference_terms = entry.get("self_reference_terms") or []
        group.adjacent_groups = entry.get("adjacent_groups") or []
        group.description = entry.get("description")
        group.enabled = entry.get("enabled", True)
        group.pack_source, group.pack_version = name, version
        await session.flush()
        slug_to_id[slug] = group.id
        group_rows[slug] = group
        counts["target_groups"] += 1

    # --- severity levels ------------------------------------------------------
    for entry in _entries(_load_yaml(pack_dir / "severity_levels.yaml")) or DEFAULT_SEVERITY_LEVELS:
        level = (
            await session.execute(
                select(SeverityLevel).where(SeverityLevel.ordinal == entry["ordinal"])
            )
        ).scalar_one_or_none()
        if level is None:
            level = SeverityLevel(ordinal=entry["ordinal"])
            session.add(level)
        level.label = entry["label"]
        level.description = entry.get("description")
        level.auto_escalate = entry.get("auto_escalate", False)
        level.pack_source, level.pack_version = name, version
        counts["severity_levels"] += 1

    # --- lexicon --------------------------------------------------------------
    for entry in _entries(_load_yaml(pack_dir / "lexicon.yaml")):
        term = entry["term"]
        scope, groups = _resolve_scope(entry, slug_to_id, group_rows)
        row = (
            await session.execute(select(LexiconEntry).where(LexiconEntry.term == term))
        ).scalar_one_or_none()
        if row is None:
            row = LexiconEntry(term=term)
            session.add(row)
        row.scope = scope
        row.target_groups = groups
        row.dialect = entry.get("dialect") or []
        row.script = entry.get("script") or []
        row.is_explicit = entry.get("is_explicit", True)
        row.severity = entry.get("severity")
        row.category = entry.get("category")
        row.variants = entry.get("variants") or []
        row.never_flag_when = entry.get("never_flag_when") or []
        row.source = entry.get("source")
        row.added_by = entry.get("added_by")
        row.enabled = entry.get("enabled", True)
        row.pack_source, row.pack_version = name, version
        counts["lexicon"] += 1

    # --- tropes ---------------------------------------------------------------
    for entry in _entries(_load_yaml(pack_dir / "tropes.yaml")):
        trope_id = entry["trope_id"]
        row = (
            await session.execute(
                select(TropeDictionaryEntry).where(TropeDictionaryEntry.trope_id == trope_id)
            )
        ).scalar_one_or_none()
        if row is None:
            row = TropeDictionaryEntry(trope_id=trope_id)
            session.add(row)
        scope, groups = _resolve_scope(entry, slug_to_id, group_rows)
        row.scope = scope
        row.target_groups = groups
        row.surface_forms = entry.get("surface_forms") or []
        row.activation = entry.get("activation") or {}
        row.implicature = entry.get("implicature")
        row.severity = entry.get("severity")
        row.category = entry.get("category")
        row.is_visual = bool(entry.get("is_visual"))
        row.visual_form = entry.get("visual_form")
        row.positive_examples = entry.get("positive_examples") or []
        row.negative_examples = entry.get("negative_examples") or []
        row.counter_speech_examples = entry.get("counter_speech_examples") or []
        row.confirmed_in_cases = entry.get("confirmed_in_cases") or []
        row.enabled = entry.get("enabled", True)
        row.pack_source, row.pack_version = name, version
        counts["tropes"] += 1

    await session.commit()
    log.info("Pack installed", pack=name, version=version, **counts)
    return counts


async def export_pack(session: AsyncSession, out_dir: Path, *, name: str, version: str) -> Path:
    """Write the current database contents back out as a pack directory."""
    out_dir.mkdir(parents=True, exist_ok=True)

    groups = (
        await session.execute(select(TargetGroup).order_by(TargetGroup.slug))
    ).scalars().all()
    id_to_slug = {g.id: g.slug for g in groups}

    (out_dir / "pack.yaml").write_text(
        yaml.safe_dump({"name": name, "version": version}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    (out_dir / "target_groups.yaml").write_text(
        yaml.safe_dump(
            [
                {
                    "slug": g.slug,
                    "display_name": {
                        "en": g.display_name_en,
                        "ar": g.display_name_ar,
                        "ku": g.display_name_ku,
                    },
                    "aliases": g.aliases,
                    "self_reference_terms": g.self_reference_terms,
                    "adjacent_groups": g.adjacent_groups,
                    "enabled": g.enabled,
                }
                for g in groups
            ],
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    lex = (await session.execute(select(LexiconEntry).order_by(LexiconEntry.term))).scalars().all()
    (out_dir / "lexicon.yaml").write_text(
        yaml.safe_dump(
            {
                "entries": [
                    {
                        "term": e.term,
                        "scope": "universal" if e.scope == TermScope.UNIVERSAL else "group_specific",
                        "target_groups": e.group_slugs,
                        "dialect": e.dialect,
                        "script": e.script,
                        "is_explicit": e.is_explicit,
                        "severity": e.severity,
                        "category": e.category,
                        "variants": e.variants,
                        "never_flag_when": e.never_flag_when,
                        "source": e.source,
                        "added_by": e.added_by,
                    }
                    for e in lex
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    tropes = (
        await session.execute(select(TropeDictionaryEntry).order_by(TropeDictionaryEntry.trope_id))
    ).scalars().all()
    (out_dir / "tropes.yaml").write_text(
        yaml.safe_dump(
            {
                "entries": [
                    {
                        "trope_id": t.trope_id,
                        "scope": "universal" if t.scope == TermScope.UNIVERSAL else "group_specific",
                        "target_groups": t.group_slugs,
                        "surface_forms": t.surface_forms,
                        "activation": t.activation,
                        "implicature": t.implicature,
                        "severity": t.severity,
                        "category": t.category,
                        "is_visual": t.is_visual,
                        "visual_form": t.visual_form,
                        "positive_examples": t.positive_examples,
                        "negative_examples": t.negative_examples,
                        "counter_speech_examples": t.counter_speech_examples,
                        "confirmed_in_cases": t.confirmed_in_cases,
                    }
                    for t in tropes
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    log.info("Pack exported", path=str(out_dir), groups=len(groups), tropes=len(tropes))
    return out_dir

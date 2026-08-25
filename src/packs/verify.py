"""Structural validation of a knowledge pack, before anything touches the database.

The rules here are not style checks — each one corresponds to a way the classifier
fails silently in production:

* a trope with no negative examples produces a system that flags all devout speech
* a lexicon entry with no source cannot be audited or defended
* a reference to an undeclared target group is a trope that never fires
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

REQUIRED_FILES = ("pack.yaml", "target_groups.yaml", "lexicon.yaml", "tropes.yaml")


@dataclass
class VerifyResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _load_yaml(path: Path):
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _entries(doc, key: str = "entries") -> list:
    """Accept either a bare list or a {entries: [...]} mapping."""
    if doc is None:
        return []
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict):
        return doc.get(key) or []
    return []


def _category_errors(entry: dict, label: str) -> list[str]:
    """Reject an unknown category rather than storing it.

    Learned from the Ettok side: their gap-approval view silently rewrites an
    unrecognised category to 'slur' and tells nobody, so a curator approving a
    `mockery` term gets a slur in the lexicon and never finds out. Failing at import
    is the only version of this that a human notices.
    """
    from src.classifiers.categories import BY_SLUG

    category = entry.get("category")
    if category and category not in BY_SLUG:
        return [
            f"{label}: unknown category {category!r}. Valid: {sorted(BY_SLUG)}"
        ]
    return []


def _scope_errors(entry: dict, slugs: set[str], label: str) -> list[str]:
    """Validate an entry's group scope.

    "Applies to every group" must be written as `scope: universal`. An entry that
    simply names no groups is a mistake, not a shortcut — silently treating it as
    universal is how a single bad row starts flagging ordinary speech everywhere.
    """
    if str(entry.get("scope", "")).lower() == "universal":
        if entry.get("target_groups") or entry.get("target_group"):
            return [f"{label}: scope is universal but target groups are also listed"]
        return []

    named = entry.get("target_groups")
    if named is None:
        single = entry.get("target_group")
        named = [single] if single else []

    if not named:
        return [
            f"{label}: no target groups — write `scope: universal` if it really "
            "applies to every group"
        ]
    return [f"{label}: undeclared target group {g!r}" for g in named if g not in slugs]


def verify_pack(pack_dir: Path, *, strict_gold: bool = False) -> VerifyResult:
    """Validate a pack directory. `strict_gold` also enforces the annotated slice."""
    result = VerifyResult()

    for name in REQUIRED_FILES:
        if not (pack_dir / name).exists():
            result.errors.append(f"missing required file: {name}")
    if result.errors:
        return result

    meta = _load_yaml(pack_dir / "pack.yaml") or {}
    for key in ("name", "version"):
        if not meta.get(key):
            result.errors.append(f"pack.yaml: missing '{key}'")
    if meta.get("license") in (None, "", "TBD"):
        result.warnings.append("pack.yaml: license is unset — required before redistribution")

    groups = _entries(_load_yaml(pack_dir / "target_groups.yaml"))
    slugs: set[str] = set()
    for i, group in enumerate(groups):
        slug = group.get("slug")
        if not slug:
            result.errors.append(f"target_groups[{i}]: missing 'slug'")
            continue
        if slug in slugs:
            result.errors.append(f"target_groups: duplicate slug {slug!r}")
        slugs.add(slug)
        if not group.get("display_name", {}).get("en"):
            result.errors.append(f"target_groups[{slug}]: missing display_name.en")
        if not group.get("aliases"):
            result.warnings.append(
                f"target_groups[{slug}]: no aliases — every missing spelling is a silent miss"
            )

    for i, entry in enumerate(_entries(_load_yaml(pack_dir / "lexicon.yaml"))):
        label = entry.get("term", f"index {i}")
        if not entry.get("term"):
            result.errors.append(f"lexicon[{i}]: missing 'term'")
        if not entry.get("source"):
            result.errors.append(f"lexicon[{label}]: missing 'source' — provenance is mandatory")
        result.errors.extend(_scope_errors(entry, slugs, f"lexicon[{label}]"))
        result.errors.extend(_category_errors(entry, f"lexicon[{label}]"))

    for i, trope in enumerate(_entries(_load_yaml(pack_dir / "tropes.yaml"))):
        label = trope.get("trope_id", f"index {i}")
        if not trope.get("trope_id"):
            result.errors.append(f"tropes[{i}]: missing 'trope_id'")
        if not trope.get("source"):
            # Same rule as a lexicon term. A trope decides what gets flagged, so it
            # has to be as traceable as a term when someone challenges a report.
            result.errors.append(f"tropes[{label}]: missing 'source' — provenance is mandatory")
        result.errors.extend(_scope_errors(trope, slugs, f"tropes[{label}]"))
        result.errors.extend(_category_errors(trope, f"tropes[{label}]"))
        # A trope needs *something* to recognise it by, but not necessarily a literal
        # string. Pattern tropes — collective blame, "not an authentic people" — and
        # visual ones have no fixed wording; they are handed to the model as guidance.
        # Requiring surface_forms would reject the 40% of the field data that
        # describes a pattern rather than quoting one.
        if not trope.get("surface_forms") and not trope.get("implicature") and not trope.get("description"):
            result.errors.append(
                f"tropes[{label}]: needs either surface_forms to match or a "
                "description of the pattern to recognise"
            )
        if not trope.get("negative_examples"):
            result.errors.append(
                f"tropes[{label}]: no negative_examples — without the benign half of the "
                "minimal pair this trope will flag ordinary speech"
            )
        if not trope.get("activation", {}).get("requires_target_group"):
            result.warnings.append(
                f"tropes[{label}]: requires_target_group is not set; the trope can fire "
                "without group context, which FR-CL-8 forbids for coded speech"
            )

    gold_path = pack_dir / "gold_eval.jsonl"
    if gold_path.exists():
        annotated: dict[str, int] = {}
        for lineno, line in enumerate(gold_path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                result.errors.append(f"gold_eval.jsonl:{lineno}: invalid JSON ({exc.msg})")
                continue
            group = item.get("target_group")
            if group and group not in slugs:
                result.errors.append(f"gold_eval.jsonl:{lineno}: undeclared target_group {group!r}")
            if item.get("label") not in ("hate", "benign", "ambiguous"):
                result.errors.append(f"gold_eval.jsonl:{lineno}: label must be hate/benign/ambiguous")
            if len(item.get("annotators") or []) >= 2 and group:
                annotated[group] = annotated.get(group, 0) + 1

        if strict_gold:
            for slug in slugs:
                if annotated.get(slug, 0) < 100:
                    result.errors.append(
                        f"gold_eval: {slug} has {annotated.get(slug, 0)} double-annotated items, "
                        "needs 100 before the pack can be promoted"
                    )
    else:
        result.warnings.append("no gold_eval.jsonl — nothing can be eval-gated without it")

    return result

"""Validate a filled curator workbook and convert it to pack YAML.

Closes the loop: staff fill the .xlsx, this checks it and emits
`packs/<name>/lexicon.yaml` and `tropes.yaml`, which `ankedo pack install` loads.

Validation is strict on purpose. Every rule here corresponds to a way the classifier
fails *silently* in production rather than loudly at import:

* a missing `source` produces a term nobody can defend when a report is challenged
* an unknown `target_group` produces a term that never matches its trope, with no error
* a trope with no `negative_examples` flags ordinary devout speech
* a trope with no `activation_topics` either never fires or fires on everything,
  depending on how it is read

Better to reject a row now than to discover it months later in the eval.

Run:  python tools/import_lexicon_sheet.py filled.xlsx packs/iraq-minorities
      python tools/import_lexicon_sheet.py filled.xlsx --check   # validate only
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from openpyxl import load_workbook

VALID_GROUPS = {
    "yazidi", "christian-iraqi", "shabak", "kakai",
    "sabian-mandaean", "turkmen-iraqi", "faili-kurd", "bahai", "kurdish",
}
VALID_CATEGORIES = {"slur", "threat", "dehumanization", "incitement"}
VALID_LANGUAGES = {"ar", "ku"}
VALID_CONTEXTS = {"news_quotation", "academic", "counter_speech", "reclaimed"}

# The grey example rows shipped in the template are marked with this prefix, so a
# curator can leave them in place as a reference. Matching on the source *text* was
# too fragile — a real curated row may legitimately cite the same transcript row.
EXAMPLE_MARKER = "EXAMPLE"


@dataclass
class Result:
    lexicon: list[dict] = field(default_factory=list)
    tropes: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def _split(value) -> list[str]:
    """Split a multi-value cell. Curators use several separators interchangeably."""
    raw = _text(value)
    if not raw:
        return []
    for separator in ("،", "؛", ";", "|", "/"):
        raw = raw.replace(separator, ",")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _is_example(source: str) -> bool:
    return source.upper().startswith(EXAMPLE_MARKER)


def _severity(value, where: str, result: Result) -> int | None:
    raw = _text(value)
    if not raw:
        result.errors.append(f"{where}: severity_weight is required")
        return None
    try:
        number = int(float(raw))
    except ValueError:
        result.errors.append(f"{where}: severity_weight {raw!r} is not a number")
        return None
    if not 1 <= number <= 10:
        result.errors.append(f"{where}: severity_weight {number} is outside 1-10")
        return None
    return number


def _groups(value, where: str, result: Result) -> list[str]:
    groups = _split(value)
    if not groups:
        result.errors.append(f"{where}: target_groups is required")
        return []
    unknown = [g for g in groups if g not in VALID_GROUPS]
    if unknown:
        result.errors.append(
            f"{where}: unknown target group(s) {unknown} — a term filed under an "
            f"unknown group silently never matches its trope. Valid: {sorted(VALID_GROUPS)}"
        )
    return [g for g in groups if g in VALID_GROUPS]


def read_lexicon(ws, result: Result) -> None:
    for index, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or not _text(row[0]):
            continue
        source = _text(row[9]) if len(row) > 9 else ""
        if _is_example(source):
            result.skipped += 1
            continue

        where = f"LEXICON row {index}"
        term = _text(row[0])

        if not source:
            result.errors.append(
                f"{where}: notes/source is required — an unsourced term cannot be "
                "defended when a report is challenged"
            )

        language = _text(row[2]).lower()
        if language and language not in VALID_LANGUAGES:
            result.errors.append(f"{where}: language {language!r} must be ar or ku")

        category = _text(row[3]).lower()
        if category and category not in VALID_CATEGORIES:
            result.errors.append(f"{where}: category {category!r} not in {sorted(VALID_CATEGORIES)}")

        explicit_raw = _text(row[5]).lower()
        if explicit_raw not in ("yes", "no"):
            result.errors.append(
                f"{where}: is_explicit must be yes or no. When unsure write no — a "
                "wrong 'yes' flags innocent speech"
            )
        is_explicit = explicit_raw == "yes"

        contexts = [c for c in _split(row[7] if len(row) > 7 else "")]
        unknown_contexts = [c for c in contexts if c not in VALID_CONTEXTS]
        if unknown_contexts:
            result.warnings.append(
                f"{where}: unrecognised never_flag_when {unknown_contexts} — ignored"
            )

        severity = _severity(row[4], where, result)
        groups = _groups(row[1], where, result)

        if is_explicit and severity and severity >= 9 and not contexts:
            result.warnings.append(
                f"{where}: severity {severity} with no never_flag_when — a term this "
                "severe will flag journalists quoting it"
            )

        result.lexicon.append({
            "term": term,
            "target_groups": groups,
            "dialect": [language] if language else [],
            "script": ["arabic"],
            "is_explicit": is_explicit,
            "severity": severity,
            "category": category or None,
            "variants": _split(row[6] if len(row) > 6 else ""),
            "never_flag_when": [c for c in contexts if c in VALID_CONTEXTS],
            "is_regex": _text(row[8] if len(row) > 8 else "").lower() == "yes",
            "source": source,
            "added_by": _text(row[10]) if len(row) > 10 else None,
        })


def read_tropes(ws, result: Result) -> None:
    for index, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or not _text(row[0]):
            continue
        source = _text(row[10]) if len(row) > 10 else ""
        if _is_example(source):
            result.skipped += 1
            continue

        where = f"TROPES row {index}"
        name = _text(row[0])

        if not _text(row[1]):
            result.errors.append(f"{where}: description is required")
        if not source:
            result.errors.append(f"{where}: notes/source is required")

        groups = _groups(row[2], where, result)
        surface_forms = _split(row[3] if len(row) > 3 else "")
        topics = _split(row[4] if len(row) > 4 else "")
        positive = _text(row[5]) if len(row) > 5 else ""
        negative = _text(row[6]) if len(row) > 6 else ""

        if not positive:
            result.errors.append(f"{where}: an example of the hateful use is required")

        if not negative:
            result.errors.append(
                f"{where}: negative_example is REQUIRED. Without the same words in a "
                "harmless setting, this pattern flags ordinary speech — for a "
                "minority-protection tool that is worse than missing hate"
            )

        if not topics:
            result.errors.append(
                f"{where}: activation_topics is required. Empty means the pattern "
                "never fires, so the row would be imported and do nothing"
            )

        severity = _severity(row[8], where, result)
        is_visual = _text(row[9] if len(row) > 9 else "").lower() == "yes"

        if is_visual and surface_forms:
            result.warnings.append(
                f"{where}: marked visual but has surface_forms — text matching will "
                "not see an image"
            )

        result.tropes.append({
            "trope_id": _slug(name),
            "target_groups": groups,
            "surface_forms": [{"text": f, "register": "unspecified"} for f in surface_forms],
            "activation": {
                "requires_target_group": True,
                "post_topic_any": topics,
                "negation_cancels": True,
            },
            "implicature": _text(row[1]),
            "severity": severity,
            "is_visual": is_visual,
            "positive_examples": [{"comment_text": positive}] if positive else [],
            "negative_examples": [{"comment_text": negative}] if negative else [],
            "counter_speech_examples": (
                [{"comment_text": _text(row[7])}] if len(row) > 7 and _text(row[7]) else []
            ),
            "confirmed_in_cases": [],
            "source": source,
        })


def _slug(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in " -" else "" for ch in name.lower())
    return "-".join(cleaned.split())[:100] or "unnamed-trope"


def convert(path: Path) -> Result:
    wb = load_workbook(path, data_only=True)
    result = Result()

    lexicon_sheet = next((n for n in wb.sheetnames if n.upper().startswith("LEXICON")), None)
    trope_sheet = next((n for n in wb.sheetnames if n.upper().startswith("TROPES")), None)

    if lexicon_sheet is None and trope_sheet is None:
        result.errors.append(
            "no LEXICON or TROPES sheet found — is this the right workbook?"
        )
        return result

    if lexicon_sheet:
        read_lexicon(wb[lexicon_sheet], result)
    if trope_sheet:
        read_tropes(wb[trope_sheet], result)

    seen: dict[str, int] = {}
    for entry in result.lexicon:
        seen[entry["term"]] = seen.get(entry["term"], 0) + 1
    for term, count in seen.items():
        if count > 1:
            result.errors.append(
                f"term {term!r} appears {count} times — use ONE row with the groups "
                "comma-separated, or two rows drift apart when only one is edited"
            )

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workbook", type=Path)
    parser.add_argument("pack_dir", type=Path, nargs="?")
    parser.add_argument("--check", action="store_true", help="validate without writing")
    args = parser.parse_args()

    if not args.workbook.exists():
        print(f"no such file: {args.workbook}")
        return 2

    result = convert(args.workbook)

    for warning in result.warnings:
        print(f"WARN  {warning}")
    for error in result.errors:
        print(f"ERROR {error}")

    print(
        f"\n{len(result.lexicon)} terms, {len(result.tropes)} tropes"
        + (f", {result.skipped} example rows skipped" if result.skipped else "")
    )

    if not result.ok:
        print(f"\n{len(result.errors)} error(s) — nothing written.")
        return 1

    if args.check or args.pack_dir is None:
        print("Valid.")
        return 0

    args.pack_dir.mkdir(parents=True, exist_ok=True)
    (args.pack_dir / "lexicon.yaml").write_text(
        yaml.safe_dump({"entries": result.lexicon}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    (args.pack_dir / "tropes.yaml").write_text(
        yaml.safe_dump({"entries": result.tropes}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"Wrote {args.pack_dir}/lexicon.yaml and tropes.yaml")
    print("Next:  ankedo pack verify && ankedo pack install")
    return 0


if __name__ == "__main__":
    sys.exit(main())

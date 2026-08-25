"""Load the gold evaluation set from a pack's JSONL file."""
from __future__ import annotations

import json
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.gold_eval_entry import GoldEvalEntry

log = structlog.get_logger()

VALID_LABELS = {"hate", "benign", "ambiguous"}


class GoldEvalLoader:
    """Loads gold evaluation data from `gold_eval.jsonl`."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def load_from_jsonl(self, path: str | Path) -> dict:
        """Import or update entries. Keyed on `id` so re-import is idempotent."""
        path = Path(path)
        created = updated = skipped = 0
        errors: list[str] = []

        existing = {
            row.external_id: row
            for row in (
                await self.session.execute(
                    select(GoldEvalEntry).where(GoldEvalEntry.external_id.is_not(None))
                )
            ).scalars()
        }

        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append(f"line {lineno}: invalid JSON ({exc.msg})")
                skipped += 1
                continue

            label = item.get("label")
            if label not in VALID_LABELS:
                errors.append(f"line {lineno}: label must be one of {sorted(VALID_LABELS)}")
                skipped += 1
                continue

            external_id = item.get("id")
            row = existing.get(external_id) if external_id else None
            if row is None:
                row = GoldEvalEntry(external_id=external_id)
                self.session.add(row)
                created += 1
            else:
                updated += 1

            row.text_content = item.get("comment_text") or item.get("text_content") or ""
            row.parent_post_text = item.get("parent_post_text")
            row.target_group = item.get("target_group")
            row.dialect = item.get("dialect")
            row.script = item.get("script")
            row.label = label
            row.category = item.get("category")
            row.severity = item.get("severity")
            row.trope_id = item.get("trope_id")
            row.annotators = item.get("annotators") or []
            row.hard_case = bool(item.get("hard_case"))
            row.why = item.get("why")
            row.source = item.get("source")
            row.license = item.get("license")

        await self.session.commit()
        log.info("Gold eval loaded", created=created, updated=updated, skipped=skipped)
        return {"created": created, "updated": updated, "skipped": skipped, "errors": errors}

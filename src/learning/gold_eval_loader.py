"""
Gold Eval Loader - Populates the GoldEvalEntry table from seed data.
"""
from __future__ import annotations

import json
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.gold_eval_entry import GoldEvalEntry

log = structlog.get_logger()


class GoldEvalLoader:
    """Loads gold evaluation data."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def load_from_json(self, file_path: str) -> int:
        """Load gold set examples from a JSON file."""
        log.info("Loading gold eval set", file_path=file_path)
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            count = 0
            for item in data:
                entry = GoldEvalEntry(
                    text_content=item["text_content"],
                    target_group=item.get("target_group"),
                    dialect=item.get("dialect"),
                    is_hate_speech=item["is_hate_speech"],
                    rationale=item.get("rationale")
                )
                self.session.add(entry)
                count += 1
                
            await self.session.commit()
            log.info("Gold eval set loaded successfully", count=count)
            return count
            
        except Exception as e:
            log.exception("Failed to load gold eval set", error=str(e))
            raise

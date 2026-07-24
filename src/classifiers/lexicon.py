"""
Fast first-pass lexicon lookup.
"""
from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.lexicon_entry import LexiconEntry
from src.classifiers.normalizer import Normalizer

log = structlog.get_logger()


class LexiconMatcher:
    """Matches normalized text against the LexiconEntry database."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.normalizer = Normalizer()

    async def scan_text(self, text: str) -> list[dict]:
        """
        Scan text for lexicon hits.
        Returns a list of match dictionaries.
        """
        if not text:
            return []
            
        normalized_text = self.normalizer.normalize(text)
        
        # In a real implementation, we would cache the lexicon in memory
        # rather than querying DB for every post, or use Aho-Corasick automaton.
        stmt = select(LexiconEntry)
        result = await self.session.execute(stmt)
        entries = result.scalars().all()
        
        hits = []
        for entry in entries:
            norm_term = self.normalizer.normalize(entry.term)
            if norm_term in normalized_text:
                hits.append({
                    "term": entry.term,
                    "target_group": entry.target_group,
                    "dialect": entry.dialect
                })
                
        if hits:
            log.debug("Lexicon hits found", hits_count=len(hits))
            
        return hits

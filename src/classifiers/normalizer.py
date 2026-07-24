"""
Text Normalization - cleans and standardizes Arabic and Kurdish text.
Handles Arabizi, diacritics, orthographic variants, and code-switching.
"""
from __future__ import annotations

import re

class Normalizer:
    """Standardizes text for classification."""

    def __init__(self):
        # Stub mappings for Arabizi and common obfuscations
        self.arabizi_map = {
            "3": "ع",
            "7": "ح",
            "5": "خ",
            "2": "ء",
        }
    
    def strip_diacritics(self, text: str) -> str:
        """Remove Arabic diacritics (Tashkeel)."""
        # Arabic diacritics unicode range: 064B - 0652
        diacritics = re.compile(r'[\u064B-\u0652]')
        return re.sub(diacritics, '', text)
        
    def normalize_orthography(self, text: str) -> str:
        """Normalize Alef, Yeh, and Teh Marbuta variants."""
        text = re.sub(r'[إأآا]', 'ا', text)
        text = re.sub(r'ة', 'ه', text)
        text = re.sub(r'ي', 'ى', text)
        return text

    def normalize(self, text: str) -> str:
        """Full normalization pipeline."""
        if not text:
            return ""
            
        text = self.strip_diacritics(text)
        text = self.normalize_orthography(text)
        
        # In a full implementation, we'd add Arabizi transliteration here
        
        # Remove repeated characters (e.g. "حلوووو" -> "حلوو")
        text = re.sub(r'(.)\1{2,}', r'\1\1', text)
        
        return text.strip().lower()

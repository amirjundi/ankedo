"""
Text normalization for Arabic and Kurdish.

MIRRORS Ettok's `apps/hate_speech/normalize.py` and must stay byte-for-byte
equivalent. The lexicon lives on the platform (see docs/AGENT_CONTRACT.md), so
prefiltering here only works if both sides reduce text to the same canonical
form. Any change to one side has to be made to the other, or matching silently
degrades — no error, just missed hate speech.

Divergence found and fixed 2026-08-25: this file previously folded yeh onto alef
maksura (ى) while Ettok folds onto yeh (ي), which would have broken matching for
every term containing either letter.
"""
from __future__ import annotations

import re

# Harakat, superscript alef and tatweel are decoration, never lexical.
_STRIP = re.compile("[ً-ْٰـ]")

_FOLD = str.maketrans(
    {
        # Alef variants — the failure the Duhok transcript demonstrates:
        # "عبدة الشيطان" vs "الشيطآن" is the same word with a different alef.
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        # Alef maqsura, and the Farsi/Kurdish yeh that Kurdish keyboards produce.
        "ى": "ي",
        "ی": "ي",
        # Ta marbuta is written as ha throughout Iraqi dialect.
        "ة": "ه",
        # Farsi/Kurdish kaf.
        "ک": "ك",
        # Arabic-Indic digits — the transcript writes the genocide year as ٢٠١٤.
        "٠": "0",
        "١": "1",
        "٢": "2",
        "٣": "3",
        "٤": "4",
        "٥": "5",
        "٦": "6",
        "٧": "7",
        "٨": "8",
        "٩": "9",
    }
)

# Deliberately NOT folded: ئ ؤ ە ێ ۆ ڕ ڵ. Standard Arabic search normalization
# collapses hamza carriers into alef, which would destroy Kurdish orthography —
# ئێزیدی is not a misspelling of anything.


def normalize(text: str) -> str:
    """Return text in the canonical form used for lexicon matching."""
    if not text:
        return ""
    return " ".join(_STRIP.sub("", text).translate(_FOLD).lower().split())


class Normalizer:
    """Object wrapper, kept because callers already hold an instance."""

    def normalize(self, text: str) -> str:
        return normalize(text)

    def strip_diacritics(self, text: str) -> str:
        return _STRIP.sub("", text)

    def normalize_orthography(self, text: str) -> str:
        return text.translate(_FOLD)

"""AnkEdo's normalizer must agree with Ettok's, character for character.

The lexicon lives on the platform and AnkEdo prefilters against it locally
(docs/AGENT_CONTRACT.md, "Where the data lives"). Prefiltering only works if both
sides reduce text to the same canonical form, and a divergence produces no error —
just terms that quietly stop matching.

This test loads Ettok's normalize.py directly when the repo is present next door,
so drift is caught the moment either side changes. It skips rather than fails when
Ettok is not checked out, since AnkEdo ships independently.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from src.classifiers.normalizer import normalize

ETTOK_NORMALIZE = (
    Path("C:/xampp/htdocs/Ettok.net/news_platform/apps/hate_speech/normalize.py")
)

# Cases chosen to cover every fold, plus the ones that previously diverged.
CORPUS = [
    "اعوذ بالله من الشيطان الرجيم",
    "عبدة الشيطان",
    "الشيطآن",                      # different alef — the transcript's own example
    "الإيزيديين",
    "الايزيديين",
    "ئێزیدی",                        # Kurdish: hamza carriers must survive
    "ڕۆژ باش",                       # Kurdish ڕ ۆ must survive
    "كوردی",                         # Farsi kaf vs Arabic kaf
    "٢٠١٤",                          # Arabic-Indic digits
    "مُحَمَّد",                          # harakat
    "الــــعراق",                     # tatweel
    "  spaced   out  text  ",
    "MiXeD Case English",
    "",
]


def _load_ettok():
    spec = importlib.util.spec_from_file_location("ettok_normalize", ETTOK_NORMALIZE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.normalize


@pytest.mark.skipif(not ETTOK_NORMALIZE.exists(), reason="Ettok.net not checked out")
@pytest.mark.parametrize("text", CORPUS)
def test_matches_ettok(text):
    assert normalize(text) == _load_ettok()(text)


def test_kurdish_hamza_carriers_survive():
    """ئێزیدی is a word, not a misspelling.

    Standard Arabic search normalization collapses hamza carriers into alef, which
    would turn this into "اىزىدى" and destroy the word. Only the Farsi yeh folds —
    ئ and ێ must come through untouched.
    """
    assert normalize("ئێزیدی") == "ئێزيدي"
    assert normalize("ڕۆژ باش") == "ڕۆژ باش"  # ڕ ۆ untouched


def test_alef_variants_fold_together():
    assert normalize("الشيطآن") == normalize("الشيطان")


def test_yeh_folds_toward_arabic_yeh():
    """Regression: this side used to fold onto ى, which broke matching against Ettok."""
    assert normalize("کوردی") == normalize("كوردي")

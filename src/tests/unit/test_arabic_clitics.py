"""Arabic attaches its function words to the next word, and the matcher missed them.

`نجس` matched. `ونجس` — the same word with "and" — did not, because the boundary
check treats the و as part of the word. Measured against the seeded pack before the
fix: ونجس, بنجس, والكفار, وعملاء and للعملاء all missed while every bare form hit.

و is among the commonest characters in written Arabic. This was not an edge case; it
was a large share of ordinary sentences going unseen. And it fails in the quiet
direction — nothing errors, the queue just stays emptier than it should, and the
agent reads as though there were less hate speech than there is.

The clitic set is closed on purpose. A general prefix-stripper would let `عملاء`
match inside unrelated words, and a false hit here puts a real person into a
human-rights record.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

import src.core.database  # noqa: F401  — registers every model with the mapper
from src.core.settings import get_settings
from src.models.lexicon_entry import LexiconEntry, TermScope


@pytest_asyncio.fixture
async def matcher(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'lex.db'}")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import get_session, init_db

    await init_db()

    from src.classifiers.lexicon import LexiconMatcher

    LexiconMatcher.invalidate_cache()

    async with get_session() as session:
        session.add(
            LexiconEntry(
                term="نجس", category="dehumanization", severity=8,
                is_explicit=True, scope=TermScope.UNIVERSAL,
                dialect=["ar"], script=["arabic"], variants=[],
            )
        )
        session.add(
            LexiconEntry(
                term="عملاء", category="disloyalty", severity=6,
                is_explicit=True, scope=TermScope.UNIVERSAL,
                dialect=["ar"], script=["arabic"], variants=[],
            )
        )
        await session.commit()
        yield LexiconMatcher(session)

    LexiconMatcher.invalidate_cache()
    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


@pytest.mark.parametrize(
    "form,clitic",
    [
        ("نجس", "bare"),
        ("ونجس", "و — and"),
        ("فنجس", "ف — so"),
        ("بنجس", "ب — with"),
        ("كنجس", "ك — like"),
        ("لنجس", "ل — for"),
        ("النجس", "ال — the"),
        ("والنجس", "و + ال"),
        ("بالنجس", "ب + ال"),
        ("للنجس", "ل + ال, contracted"),
    ],
)
async def test_a_term_matches_through_its_proclitics(matcher, form, clitic):
    hits = await matcher.scan_text(f"هذوله {form} هنا")

    assert hits, f"missed {form} ({clitic})"
    assert hits[0]["matched"] == "نجس", "the hit should key on the bare term"


async def test_the_hit_reports_the_bare_term_not_the_prefixed_string(matcher):
    """The index is keyed on the term. If the clitic came back in `matched`, the
    lookup would miss and every prefixed hit would raise instead."""
    hits = await matcher.scan_text("هذوله والنجس هنا")

    assert hits[0]["matched"] == "نجس"
    assert hits[0]["severity"] == 8
    assert hits[0]["category"] == "dehumanization"


async def test_two_terms_in_one_sentence_are_both_found(matcher):
    """The sentence that exposed this: كفار matched and ونجس, three words later, did
    not — so the sentence looked half as bad as it was."""
    hits = await matcher.scan_text("هذوله عملاء ونجس ما يصير تاكل من ايدهم")

    assert {h["matched"] for h in hits} == {"عملاء", "نجس"}


@pytest.mark.parametrize(
    "sentence",
    [
        "زرت لالش قبل سنة، مكان هادئ وناسه طيبين",
        "الطقس اليوم حار جدا في الموصل",
        "اجتمع مجلس المحافظة لمناقشة الخدمات",
        "المدرسة الجديدة فتحت ابوابها للطلاب",
        "الفريق فاز بالمباراة بثلاثة اهداف",
        "تكلمنا وياهم عن العمل والمستقبل",
    ],
)
async def test_ordinary_sentences_stay_clean(matcher, sentence):
    """The other half of the change. Loosening the left boundary must not start
    matching inside unrelated words — a false hit here names a real person in an
    evidence file."""
    assert await matcher.scan_text(sentence) == []


async def test_a_term_inside_a_longer_word_is_still_not_a_match(matcher):
    """The right boundary is unchanged and must stay strict: a clitic may precede the
    term, but the term must still end where the word ends."""
    assert await matcher.scan_text("هذا نجسيات شي ثاني") == []

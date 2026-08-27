"""never_flag_when and self_reference_terms, and the limits on both.

Both columns were carried from the workbook into the database and read by nothing.
`never_flag_when` reached the specialist as a line of prompt text it could ignore;
`self_reference_terms` was empty for all eight groups and consulted by no classifier.
A curator filling either was doing work that changed no outcome.

They matter because the same words are a different act in a different mouth. The
person most likely to quote a libel verbatim is the person arguing against it, and
this system's output becomes a human-rights record — so flagging them is not a small
error, it is the specific harm the project can cause.

The tests below are split between the two directions, and the second half matters more
than the first. An exemption must fire when the dictionary says the context excuses
the term, and must NOT fire on an incitement term, on a mixed comment, or because a
model said the magic word.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

import src.core.database  # noqa: F401  — registers every model with the mapper
from src.classifiers.exemptions import (
    COUNTER_SPEECH,
    NEWS_QUOTATION,
    RECLAIMED,
    ExemptionChecker,
)
from src.core.settings import get_settings
from src.models.target_group import TargetGroup


def _hit(term, *, never_flag=(), in_scope=True):
    return {
        "term": term,
        "matched": term,
        "in_scope": in_scope,
        "never_flag_when": list(never_flag),
        "is_explicit": True,
    }


# ── the rule itself ──────────────────────────────────────────────────────────


def test_a_term_that_excuses_itself_in_this_context_is_exempt():
    hits = [_hit("عبدة الشيطان", never_flag=["counter_speech", "news_quotation"])]

    exemption = ExemptionChecker.check(hits, {COUNTER_SPEECH})

    assert exemption is not None
    assert exemption.signal == COUNTER_SPEECH
    assert exemption.terms == ["عبدة الشيطان"]


def test_no_signal_means_no_exemption():
    hits = [_hit("عبدة الشيطان", never_flag=["counter_speech"])]

    assert ExemptionChecker.check(hits, set()) is None


def test_a_term_without_the_rule_is_not_exempt():
    """اقتلوهم lists only news_quotation and academic. No amount of in-group
    reclamation excuses incitement, and the dictionary says so."""
    hits = [_hit("اقتلوهم", never_flag=["news_quotation", "academic"])]

    assert ExemptionChecker.check(hits, {RECLAIMED}) is None


def test_one_unexcused_term_keeps_the_flag_for_all_of_them():
    """The most important rule here. A comment that quotes a libel *and* says "kill
    them" is not counter-speech, and exempting it because one of its terms was
    quotable would be a straightforward way to smuggle incitement past the flag."""
    hits = [
        _hit("عبدة الشيطان", never_flag=["counter_speech"]),
        _hit("اقتلوهم", never_flag=["news_quotation"]),
    ]

    assert ExemptionChecker.check(hits, {COUNTER_SPEECH}) is None


def test_out_of_scope_hits_are_not_counted():
    """A group-specific term that did not apply to this post is not evidence, so it
    should neither block an exemption nor grant one."""
    hits = [
        _hit("نسطوري", never_flag=["academic"], in_scope=False),
        _hit("عبدة الشيطان", never_flag=["counter_speech"], in_scope=True),
    ]

    assert ExemptionChecker.check(hits, {COUNTER_SPEECH}) is not None


def test_a_trope_only_verdict_is_exempt_on_a_self_contradiction():
    """The case that matters most, and the one the first version of this rule missed.

    Counter-speech usually quotes a libel no dictionary holds, so the trope fires and
    no term does. Requiring a lexicon hit left the exact scenario the rule exists for
    uncovered — the defender was still flagged."""
    exemption = ExemptionChecker.check([], {COUNTER_SPEECH})

    assert exemption is not None
    assert exemption.signal == COUNTER_SPEECH
    assert exemption.terms == []


def test_reclaimed_alone_does_not_excuse_a_trope_only_verdict():
    """`reclaimed` is inferred from the text, not declared by the model. On its own it
    says only that a group's own name appears somewhere in the comment — far too
    little to withdraw a flag when no dictionary rule is involved."""
    assert ExemptionChecker.check([], {RECLAIMED}) is None


# ── self-reference detection ─────────────────────────────────────────────────


@pytest_asyncio.fixture
async def checker(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'ex.db'}")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import get_session, init_db

    await init_db()
    async with get_session() as session:
        session.add(
            TargetGroup(
                slug="yazidi", display_name_en="Yazidi",
                self_reference_terms=["ايزيدي", "ئێزیدی", "êzidî"],
                aliases=[], adjacent_groups=[],
            )
        )
        session.add(
            TargetGroup(
                slug="shabak", display_name_en="Shabak",
                self_reference_terms=[], aliases=[], adjacent_groups=[],
            )
        )
        await session.commit()
        yield ExemptionChecker(session)

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


async def test_a_comment_using_the_groups_own_name_signals_reclaimed(checker):
    signals = await checker.detect_signals(
        comment_text="احنا الايزيدي نحچي بينا هيچي",
        target_groups=["yazidi"],
        specialist={"category": "slur"},
    )

    assert RECLAIMED in signals


async def test_kurdish_script_self_reference_is_recognised(checker):
    signals = await checker.detect_signals(
        comment_text="ئێمە ئێزیدی ین", target_groups=["yazidi"], specialist={},
    )

    assert RECLAIMED in signals


async def test_a_comment_without_the_groups_own_name_does_not(checker):
    """The whole point of the gate: an outsider using a slur must not get the
    reclaimed exemption."""
    signals = await checker.detect_signals(
        comment_text="هذوله كلهم نجس", target_groups=["yazidi"], specialist={},
    )

    assert RECLAIMED not in signals


async def test_a_group_with_no_self_reference_terms_never_signals_reclaimed(checker):
    """An unfilled column must fail closed. If an empty list matched everything, every
    slur against the Shabak would be exempt."""
    signals = await checker.detect_signals(
        comment_text="احنا شبك", target_groups=["shabak"], specialist={},
    )

    assert RECLAIMED not in signals


async def test_the_specialists_own_category_can_signal_counter_speech(checker):
    signals = await checker.detect_signals(
        comment_text="يسمونهم عبدة الشيطان وهذا افتراء",
        target_groups=["yazidi"],
        specialist={"category": "counter_speech"},
    )

    assert COUNTER_SPEECH in signals


async def test_news_reporting_maps_to_the_quotation_rule(checker):
    signals = await checker.detect_signals(
        comment_text="تقرير", target_groups=["yazidi"],
        specialist={"category": "news_reporting"},
    )

    assert NEWS_QUOTATION in signals


async def test_an_ordinary_category_signals_nothing(checker):
    signals = await checker.detect_signals(
        comment_text="هذوله نجس", target_groups=["yazidi"],
        specialist={"category": "dehumanization"},
    )

    assert signals == set()


async def test_the_shipped_pack_has_self_reference_terms_for_every_group():
    """They were empty for all eight, which made the reclaimed gate unreachable. An
    empty list fails closed, so this was silent."""
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[3] / "packs" / "iraq-minorities" / "target_groups.yaml"
    groups = yaml.safe_load(path.read_text(encoding="utf-8"))

    missing = [g["slug"] for g in groups if not g.get("self_reference_terms")]
    assert not missing, f"groups whose reclaimed gate can never fire: {missing}"

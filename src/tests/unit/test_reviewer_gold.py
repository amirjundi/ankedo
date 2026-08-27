"""Human judgement has to reach the thing that measures the agent.

A reviewer confirmed or overturned a verdict, a ReviewerDecision row was written, and
nothing read it. The only mechanism that changed behaviour was calibration, fitted
from a static gold_eval.jsonl that someone edited by hand — so the agent's measured
accuracy was frozen against a file, and the people doing the actual judging fed back
nothing at all.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

# Importing this registers every model with the mapper. Comment declares a
# relationship to EvidencePackage, so importing Comment alone leaves the registry
# half-built and the first query raises "failed to locate a name".
import src.core.database  # noqa: F401
from src.core.settings import get_settings
from src.learning.reviewer_gold import (
    SOURCE,
    external_id_for,
    promote_reviewed_decisions,
    true_label,
)
from src.models.comment import Comment
from src.models.gold_eval_entry import GoldEvalEntry
from src.models.post import Post, QueueState
from src.models.reviewer_decision import ReviewerDecision
from src.models.tracked_account import AccountSource, AccountStatus, TrackedAccount


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'g.db'}")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import get_session, init_db

    await init_db()
    async with get_session() as s:
        yield s

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


async def _reviewed(session, *, agent_said: bool, confirmed: bool, text="نص التعليق"):
    account = TrackedAccount(
        platform="facebook", handle="p", status=AccountStatus.ACTIVE, source=AccountSource.MANUAL
    )
    session.add(account)
    await session.flush()

    post = Post(
        tracked_account_id=account.id, platform="facebook", platform_post_id="p1",
        url="https://x/1", content_text="مراسم دينية إيزيدية في معبد لالش",
        queue_state=QueueState.REVIEW, dialect="iraqi",
    )
    session.add(post)
    await session.flush()

    comment = Comment(post_id=post.id, platform_comment_id="c1", text=text)
    session.add(comment)
    await session.flush()

    decision = ReviewerDecision(
        post_id=post.id, comment_id=comment.id, reviewer_id="reviewer_1",
        original_hate_speech_flag=agent_said, is_confirmed=confirmed,
        reviewer_rationale="because of the parent post",
    )
    session.add(decision)
    await session.commit()
    return decision


# ── the label is the human's ─────────────────────────────────────────────────


def test_confirming_a_flag_means_it_was_hate():
    assert true_label(ReviewerDecision(original_hate_speech_flag=True, is_confirmed=True)) == "hate"


def test_overturning_a_flag_means_it_was_not():
    """The row that matters most. Writing the agent's own verdict here would make the
    gold set circular — the system grading itself against its past answers."""
    assert true_label(ReviewerDecision(original_hate_speech_flag=True, is_confirmed=False)) == "benign"


def test_overturning_a_clear_means_it_was_hate():
    """A reviewer catching something the agent missed."""
    assert true_label(ReviewerDecision(original_hate_speech_flag=False, is_confirmed=False)) == "hate"


def test_confirming_a_clear_means_benign():
    assert true_label(ReviewerDecision(original_hate_speech_flag=False, is_confirmed=True)) == "benign"


def test_a_missing_verdict_is_treated_as_not_hate():
    assert true_label(ReviewerDecision(original_hate_speech_flag=None, is_confirmed=True)) == "benign"


# ── promotion ────────────────────────────────────────────────────────────────


async def test_a_decision_becomes_a_gold_entry(session):
    decision = await _reviewed(session, agent_said=True, confirmed=True)

    assert await promote_reviewed_decisions(session) == 1

    entry = (await session.execute(select(GoldEvalEntry))).scalar_one()
    assert entry.external_id == external_id_for(decision.id)
    assert entry.label == "hate"
    assert entry.source == SOURCE
    assert entry.annotators == ["reviewer_1"]


async def test_the_parent_post_travels_with_it(session):
    """The premise of the whole system is that a comment is judged against what it
    replies to. A gold entry without its parent is evaluated under conditions the
    agent never actually sees."""
    await _reviewed(session, agent_said=True, confirmed=True)
    await promote_reviewed_decisions(session)

    entry = (await session.execute(select(GoldEvalEntry))).scalar_one()
    assert entry.parent_post_text == "مراسم دينية إيزيدية في معبد لالش"
    assert entry.dialect == "iraqi"


async def test_an_overturned_decision_is_marked_hard(session):
    """Where the agent and a human disagreed is worth more than a hundred it got
    trivially right — and is exactly what a static gold file never contains."""
    await _reviewed(session, agent_said=True, confirmed=False)
    await promote_reviewed_decisions(session)

    entry = (await session.execute(select(GoldEvalEntry))).scalar_one()
    assert entry.hard_case is True
    assert entry.label == "benign"


async def test_a_confirmed_decision_is_not_marked_hard(session):
    await _reviewed(session, agent_said=True, confirmed=True)
    await promote_reviewed_decisions(session)

    assert (await session.execute(select(GoldEvalEntry))).scalar_one().hard_case is False


async def test_promoting_twice_does_not_duplicate(session):
    """The loop runs this every cycle."""
    await _reviewed(session, agent_said=True, confirmed=True)

    assert await promote_reviewed_decisions(session) == 1
    assert await promote_reviewed_decisions(session) == 0

    assert len((await session.execute(select(GoldEvalEntry))).scalars().all()) == 1


async def test_a_decision_with_no_text_is_skipped(session):
    """A gold entry with nothing to classify would be scored every run and mean
    nothing every time."""
    await _reviewed(session, agent_said=True, confirmed=True, text="   ")

    assert await promote_reviewed_decisions(session) == 0
    assert (await session.execute(select(GoldEvalEntry))).scalars().all() == []


async def test_the_reviewers_reasoning_is_kept(session):
    await _reviewed(session, agent_said=True, confirmed=False)
    await promote_reviewed_decisions(session)

    entry = (await session.execute(select(GoldEvalEntry))).scalar_one()
    assert entry.why == "because of the parent post"


async def test_entries_from_the_static_file_are_left_alone(session):
    """Promotion looks only at its own source, so a hand-curated gold set is not
    touched, renumbered or deduplicated against reviewer data."""
    session.add(
        GoldEvalEntry(
            external_id="curated:1", text_content="نص", label="hate", source="gold_eval.jsonl"
        )
    )
    await session.commit()
    await _reviewed(session, agent_said=True, confirmed=True)

    await promote_reviewed_decisions(session)

    rows = (await session.execute(select(GoldEvalEntry))).scalars().all()
    assert len(rows) == 2
    assert {r.source for r in rows} == {"gold_eval.jsonl", SOURCE}

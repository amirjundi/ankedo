"""The review queue endpoint, and the payload the dashboard needs from it.

The submit button in the UI never called this — it logged to the console and advanced.
A reviewer worked through the queue believing their judgements were recorded and
nothing was written, which quietly discarded the one thing the whole system exists to
collect. The page is wired now; these pin the contract it relies on so the endpoint
cannot drift out from under it.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.core.settings import get_settings
from src.models.post import Post, QueueState
from src.models.queue_item import QueueItem, QueueStage
from src.models.reviewer_decision import ReviewerDecision
from src.models.tracked_account import AccountSource, AccountStatus, TrackedAccount

# Everything the dashboard reads off an item. A field dropped here is a blank panel.
REQUIRED_FIELDS = {
    "queue_item_id", "post_id", "platform", "content", "author", "score",
    "trace", "tropes_fired", "target_group", "case_title", "prior_confirmed",
}


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'review.db'}")
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


async def _queued_post(session, *, author="Some Page", flagged=True):
    account = TrackedAccount(
        platform="facebook", handle="somepage", status=AccountStatus.WARMUP,
        source=AccountSource.MANUAL,
    )
    session.add(account)
    await session.flush()

    post = Post(
        tracked_account_id=account.id,
        platform="facebook",
        platform_post_id=f"p{author}",
        url="https://facebook.com/p/1",
        content_text="منشور للمراجعة",
        author_name=author,
        classification_score=0.91,
        hate_speech_flag=flagged,
        multi_agent_trace={"tropes_fired": [{"trope_id": "yazidi-devil-worship"}]},
        queue_state=QueueState.REVIEW,
    )
    session.add(post)
    await session.flush()

    item = QueueItem(stage=QueueStage.REVIEW, post_id=post.id,
                     tracked_account_id=account.id, is_inflight=False)
    session.add(item)
    await session.commit()
    return account, post, item


async def test_the_queue_returns_every_field_the_dashboard_renders(session):
    from src.api.review_endpoints import get_review_queue

    await _queued_post(session)

    payload = await get_review_queue(session)
    item = payload["queue"][0]

    missing = REQUIRED_FIELDS - set(item)
    assert not missing, f"the dashboard would render blanks for: {missing}"
    assert item["author"] == "Some Page"
    assert item["tropes_fired"], "the trope that fired is why a human is looking at this"


async def test_submitting_a_confirmation_records_a_decision(session):
    """The write the UI button was never making."""
    from src.api.review_endpoints import ReviewSubmission, submit_review

    _, post, item = await _queued_post(session)

    result = await submit_review(
        item.id, ReviewSubmission(reviewer_id="dashboard", is_confirmed=True), session
    )

    assert result["status"] == "success"
    decision = (await session.execute(select(ReviewerDecision))).scalar_one()
    assert decision.post_id == post.id
    assert decision.is_confirmed is True
    assert decision.original_hate_speech_flag is True


async def test_a_rejection_marks_the_post_rejected(session):
    from src.api.review_endpoints import ReviewSubmission, submit_review

    _, post, item = await _queued_post(session)

    await submit_review(
        item.id, ReviewSubmission(reviewer_id="dashboard", is_confirmed=False), session
    )

    await session.refresh(post)
    assert post.queue_state == QueueState.REJECTED


async def test_a_reviewed_item_leaves_the_queue(session):
    """Otherwise it comes back next load and gets judged twice."""
    from src.api.review_endpoints import ReviewSubmission, get_review_queue, submit_review

    _, _, item = await _queued_post(session)
    await submit_review(
        item.id, ReviewSubmission(reviewer_id="dashboard", is_confirmed=True), session
    )

    assert (await get_review_queue(session))["queue"] == []


async def test_an_unknown_queue_item_is_refused(session):
    from fastapi import HTTPException

    from src.api.review_endpoints import ReviewSubmission, submit_review

    with pytest.raises(HTTPException) as caught:
        await submit_review(
            "not-a-real-id", ReviewSubmission(reviewer_id="x", is_confirmed=True), session
        )

    assert caught.value.status_code == 404


# ── Prior history: evidence for the human, not input to the model ────────────


async def test_prior_confirmations_by_the_same_author_are_counted(session):
    from src.api.review_endpoints import ReviewSubmission, get_review_queue, submit_review

    account, first_post, first_item = await _queued_post(session, author="Repeat")
    await submit_review(
        first_item.id, ReviewSubmission(reviewer_id="r", is_confirmed=True), session
    )

    # A second post from the same account.
    second = Post(
        tracked_account_id=account.id, platform="facebook", platform_post_id="p2",
        url="https://facebook.com/p/2", content_text="آخر", author_name="Repeat",
        classification_score=0.8, hate_speech_flag=True, queue_state=QueueState.REVIEW,
    )
    session.add(second)
    await session.flush()
    session.add(QueueItem(stage=QueueStage.REVIEW, post_id=second.id,
                          tracked_account_id=account.id, is_inflight=False))
    await session.commit()

    item = (await get_review_queue(session))["queue"][0]
    assert item["prior_confirmed"] == 1


async def test_prior_history_never_reaches_the_classifier(session):
    """A model told an author was flagged before will flag them again, and the
    corroboration it produces is its own. This must stay reviewer-only."""
    from src.classifiers.context_bundle import ContextBundle

    fields = set(ContextBundle.__dataclass_fields__)
    leaked = {f for f in fields if "prior" in f or "history" in f or "offend" in f}
    assert not leaked, f"prior history leaked into the classifier input: {leaked}"

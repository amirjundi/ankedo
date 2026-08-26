"""One item, all the way through.

The system had never processed anything: zero posts, zero comments, zero LLM calls
in the ledger. That was not only the missing browser and the missing key — the
pipeline itself had a gap in the middle. Items were enqueued at Discovery, the
orchestration loop drained only Classification, and PostProcessor, which is supposed
to move an item between the two, was never instantiated anywhere in the codebase. A
post could not have reached a classifier however well everything else worked.

This walks a captured post from arrival to a review-queue verdict. The model is a
fake — a real one is a network call and a cost, and what needs proving here is the
wiring, not Gemini. Everything else is the production path: the real queue manager,
the real committee orchestrator, the real classification worker, the real database.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from src.classifiers.committee.schemas import (
    Category,
    CriticDecision,
    SpecialistDecision,
    TriageDecision,
    Verdict,
)
from src.core.settings import get_settings
from src.models.comment import Comment
from src.models.post import Post, QueueState
from src.models.queue_item import QueueItem, QueueStage

PIOUS = "اعوذ بالله من الشيطان الرجيم"
LALISH = "مراسم دينية إيزيدية في معبد لالش"


class FakeLLM:
    """Answers the three committee roles. Records what it was asked."""

    def __init__(self):
        self.purposes: list[str] = []

    async def generate(self, *, purpose, **kwargs):
        self.purposes.append(purpose)
        if purpose == "triage":
            return TriageDecision(requires_specialist=True, rationale="devil-worship libel")
        if purpose == "specialist":
            return SpecialistDecision(
                verdict=Verdict.HATE, confidence=0.91, category=Category.DEHUMANIZATION,
                target_group="yazidi", severity=4, relies_on_context=True,
                rationale="invokes the devil-worship libel on a Yazidi religious post",
            )
        if purpose == "critic":
            return CriticDecision(agrees_with_specialist=True, concern=None, suggested_verdict=None,
                                 rationale="supported by the parent post")
        raise AssertionError(f"unexpected purpose {purpose}")


@pytest_asyncio.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}")
    monkeypatch.setenv("EXTENSION_ENABLED", "true")
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


async def _capture(session, comments):
    """Arrive the way the Chrome extension delivers a thread."""
    from src.api.extension_router import CaptureRequest, capture

    return await capture(
        CaptureRequest(
            platform="facebook",
            url="https://facebook.com/page/posts/1",
            platform_post_id="1",
            content_text=LALISH,
            author_name="A Page",
            author_handle="apage",
            comments=[
                {"platform_comment_id": f"c{i}", "text": text, "author_name": "Someone"}
                for i, text in enumerate(comments)
            ],
        ),
        session,
    )


async def _drain_classification(session, llm):
    """Run the queue the way OrchestrationLoop._process_queues does."""
    from src.classifiers.classification_worker import ClassificationWorker
    from src.core.queue_manager import QueueManager

    queue = QueueManager(session)
    worker = ClassificationWorker(session, queue)
    # The worker lazily builds its own orchestrator behind a read-only property;
    # seed the private slot so the production code path is otherwise untouched.
    from src.classifiers.committee.orchestrator import CommitteeOrchestrator

    worker._orchestrator = CommitteeOrchestrator(session, llm=llm)

    processed = 0
    while True:
        item = await queue.dequeue(QueueStage.CLASSIFICATION, worker_id="test")
        if item is None:
            break
        await worker.process_item(item)
        processed += 1
    return processed


async def test_a_captured_post_reaches_a_verdict(session):
    """Arrival → queue → committee → review. The pass that had never happened."""
    llm = FakeLLM()

    captured = await _capture(session, [PIOUS, "صباح الخير"])
    assert captured.comments_added == 2

    processed = await _drain_classification(session, llm)

    assert processed == 1, "the captured post was never picked up for classification"
    assert "specialist" in llm.purposes, "the committee never ran"

    post = (await session.execute(select(Post))).scalar_one()
    assert post.hate_speech_flag is True
    assert post.classification_score is not None
    assert post.queue_state == QueueState.REVIEW


async def test_it_lands_in_the_review_queue_for_a_human(session):
    """The point of the pipeline is a human seeing it, not a database row."""
    await _capture(session, [PIOUS])
    await _drain_classification(session, FakeLLM())

    item = (await session.execute(select(QueueItem))).scalar_one()
    assert item.stage == QueueStage.REVIEW
    assert item.is_inflight is False


async def test_the_capture_enters_at_classification_not_discovery(session):
    """Discovery waits for a browser fetch that a captured post neither needs nor
    can have — the gap that stalled every item the pipeline ever held."""
    await _capture(session, [PIOUS])

    item = (await session.execute(select(QueueItem))).scalar_one()
    assert item.stage == QueueStage.CLASSIFICATION


async def test_the_verdict_is_recorded_against_the_comment(session):
    await _capture(session, [PIOUS])
    await _drain_classification(session, FakeLLM())

    comments = (await session.execute(select(Comment))).scalars().all()
    assert any(c.hate_speech_flag is not None for c in comments), (
        "no comment carries a classification result"
    )


async def test_the_committee_runs_in_order(session):
    await _capture(session, [PIOUS])
    llm = FakeLLM()
    await _drain_classification(session, llm)

    order = [p for p in llm.purposes if p in ("triage", "specialist", "critic")]
    assert order[:1] == ["triage"], "triage must gate the expensive calls"
    assert "critic" in order, "nothing reviewed the specialist"


async def test_nothing_is_left_in_flight_afterwards(session):
    """An item stuck in-flight is invisible to the next cycle and never retried."""
    await _capture(session, [PIOUS])
    await _drain_classification(session, FakeLLM())

    items = (await session.execute(select(QueueItem))).scalars().all()
    assert not any(i.is_inflight for i in items)

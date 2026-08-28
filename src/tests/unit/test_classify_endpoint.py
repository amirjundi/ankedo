"""The classifier as a service, so it need not be rewritten to be reused.

The committee, the lexicon, the trope engine, the Arabic normaliser and the exemption
rules are the part of this system that took the work and carries the risk. They were
reachable only from inside the Python process, which made "should we rebuild on a
TypeScript agent framework?" look like it required porting all sixteen thousand lines.

It does not. Over HTTP, a TypeScript agent or anything else gets the verdict and its
reasoning in a few hundred lines of client code.

The boundary that matters: this classifies and answers. It stores nothing. The
evidence record should hold things the agent collected and a human can trace to a
source, not everything anyone posted to an endpoint to see what it would say.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from src.core.settings import get_settings

TOKEN = "t"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

VERDICT = {
    "verdict": "hate", "hate_speech_flag": True, "confidence": 0.9,
    "category": "dehumanization", "severity": 4, "relies_on_context": True,
    "committee_disagreement": False,
    "trace": {
        "lexicon_hits": [{"term": "نجس", "matched": "نجس", "category": "dehumanization",
                          "severity": 8, "in_scope": True}],
        "tropes_fired": [{"trope_id": "ritual-impurity"}],
        "trope_candidates": [],
        "specialist": {"rationale": "aimed at the group named in the parent post",
                       "model": "big-pickle", "prompt_version": "v1"},
    },
}


@pytest_asyncio.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'x.db'}")
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    monkeypatch.setenv("RUN_AGENT_WITH_API", "false")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    get_settings.cache_clear()

    import src.models.base as base

    base._engine = None
    base._async_session_factory = None

    from src.core.database import init_db

    await init_db()

    async def fake_run(self, bundle):
        fake_run.bundle = bundle
        return VERDICT

    monkeypatch.setattr(
        "src.classifiers.committee.orchestrator.CommitteeOrchestrator.run", fake_run
    )

    from src.api.main import app

    with TestClient(app) as c:
        c.seen = fake_run
        yield c

    get_settings.cache_clear()
    base._engine = None
    base._async_session_factory = None


def test_it_requires_a_token(client):
    assert client.post("/api/classify", json={"text": "x"}).status_code in (401, 403)


def test_empty_text_is_refused(client):
    assert client.post("/api/classify", json={"text": "   "}, headers=AUTH).status_code == 400


def test_absurdly_long_text_is_refused(client):
    response = client.post(
        "/api/classify", json={"text": "x" * 20000}, headers=AUTH
    )
    assert response.status_code == 400


def test_the_verdict_and_its_evidence_come_back(client):
    body = client.post(
        "/api/classify",
        json={"text": "هذوله نجس", "parent_post_text": "مراسم إيزيدية في لالش"},
        headers=AUTH,
    ).json()

    assert body["verdict"] == "hate"
    assert body["confidence"] == 0.9
    assert body["lexicon_hits"][0]["matched"] == "نجس"
    assert body["tropes_fired"] == ["ritual-impurity"]
    assert "parent post" in body["rationale"]


def test_the_parent_post_reaches_the_classifier(client):
    """The relational question is the premise. A caller sending a parent must have it
    actually used, or the endpoint answers something else."""
    client.post(
        "/api/classify",
        json={"text": "هذوله نجس", "parent_post_text": "مراسم إيزيدية في لالش"},
        headers=AUTH,
    )

    assert client.seen.bundle.parent_post_text == "مراسم إيزيدية في لالش"


def test_the_response_says_whether_it_was_judged_in_context(client):
    """Without a parent this judges the words alone, which is not what the agent
    normally does. A caller comparing results has to be able to tell."""
    with_parent = client.post(
        "/api/classify", json={"text": "x", "parent_post_text": "y"}, headers=AUTH
    ).json()
    alone = client.post("/api/classify", json={"text": "x"}, headers=AUTH).json()

    assert with_parent["judged_in_context"] is True
    assert alone["judged_in_context"] is False


def test_explicit_target_groups_are_honoured(client):
    client.post(
        "/api/classify",
        json={"text": "x", "target_groups": ["shabak"]},
        headers=AUTH,
    )

    assert client.seen.bundle.target_groups == ["shabak"]


def test_nothing_is_written_to_the_evidence_record(client):
    """The boundary. An endpoint anyone can post to must not fill the database with
    text nobody collected and no one can trace to a source."""
    from sqlalchemy import func, select

    client.post(
        "/api/classify", json={"text": "هذوله نجس", "parent_post_text": "y"}, headers=AUTH
    )

    import asyncio

    from src.core.database import get_session
    from src.models.comment import Comment
    from src.models.outbox import OutboxItem
    from src.models.post import Post
    from src.models.queue_item import QueueItem

    async def counts():
        async with get_session() as s:
            return [
                (await s.execute(select(func.count(m.id)))).scalar_one()
                for m in (Post, Comment, QueueItem, OutboxItem)
            ]

    assert asyncio.get_event_loop().run_until_complete(counts()) == [0, 0, 0, 0]


def test_a_model_outage_is_a_502_not_a_500(client, monkeypatch):
    """The caller's request was fine. 502 tells a client to retry; 500 tells it the
    request was wrong."""
    from src.classifiers.llm_client import LLMError

    async def down(self, bundle):
        raise LLMError("connection error")

    monkeypatch.setattr(
        "src.classifiers.committee.orchestrator.CommitteeOrchestrator.run", down
    )

    response = client.post("/api/classify", json={"text": "x"}, headers=AUTH)

    assert response.status_code == 502
    assert "model unavailable" in response.json()["detail"]

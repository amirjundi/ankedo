"""Classification over HTTP, so any harness can use it.

The committee, the lexicon, the trope engine, the Arabic normaliser and the exemption
rules are the part of this system that took the work and carries the risk. They were
reachable only from inside the Python process, which made a reasonable question —
"should we rebuild on a TypeScript agent framework?" — look like it required porting
all of it.

It does not. This endpoint makes the classifier a service. A TypeScript agent, an n8n
flow, a shell script or the platform itself can send text and get back the verdict
with its reasoning, in a few hundred lines of whatever language, instead of
reimplementing sixteen thousand lines of judgement that is already tested.

**Nothing is stored.** This classifies and answers. It does not create a Post, a
Comment, a queue item or an outbox row, and nothing here reaches the review queue or
the platform. That boundary is deliberate: the evidence record should contain things
the agent collected and a human can trace back to a source, not everything anyone ever
posted to an endpoint to see what it would say.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import session_scope

log = structlog.get_logger()
router = APIRouter(prefix="/api", tags=["classify"])

MAX_TEXT = 8000


class ClassifyRequest(BaseModel):
    text: str = Field(description="The comment or post to judge")
    # The relational question is the whole premise: a comment is judged against what
    # it replies to. Optional, because a caller may genuinely have only a phrase — the
    # response says which question was answered.
    parent_post_text: str = Field(default="", description="The post it replies to")
    target_groups: list[str] = Field(
        default_factory=list,
        description="Group slugs, if known. Detected from the parent post when absent.",
    )
    dialect: str = Field(default="")


@router.post("/classify")
async def classify(body: ClassifyRequest, session: AsyncSession = Depends(session_scope)):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if len(text) > MAX_TEXT:
        raise HTTPException(
            status_code=400,
            detail=f"text is {len(text)} characters; the limit is {MAX_TEXT}",
        )

    from src.classifiers.committee.orchestrator import CommitteeOrchestrator
    from src.classifiers.context_bundle import ContextBundle
    from src.classifiers.group_resolver import GroupResolver
    from src.classifiers.llm_client import LLMError

    parent = (body.parent_post_text or "").strip()
    groups = list(body.target_groups)
    if not groups and parent:
        groups = await GroupResolver(session).resolve_all(parent)

    try:
        result = await CommitteeOrchestrator(session).run(
            ContextBundle(
                comment_text=text,
                parent_post_text=parent,
                target_groups=groups,
                dialect=body.dialect or None,
            )
        )
    except LLMError as exc:
        # The caller's request was fine; the model was not. 502 rather than 500 so a
        # client can retry rather than treating it as a bad request.
        raise HTTPException(status_code=502, detail=f"model unavailable: {exc}") from exc

    trace = result.get("trace") or {}
    specialist = trace.get("specialist") or {}

    return {
        "verdict": result["verdict"],
        "hate_speech_flag": result["hate_speech_flag"],
        "confidence": result["confidence"],
        "category": result.get("category"),
        "severity": result.get("severity", 0),
        "target_groups": groups,
        # Which question was actually answered. Without a parent post this is a
        # judgement on the words alone, which is not what the agent normally does, and
        # a caller comparing results needs to know that.
        "judged_in_context": bool(parent),
        "relies_on_context": result.get("relies_on_context", False),
        "committee_disagreement": result.get("committee_disagreement", False),
        "rationale": specialist.get("rationale") or "",
        "lexicon_hits": [
            {
                "term": h.get("term"),
                "matched": h.get("matched"),
                "category": h.get("category"),
                "severity": h.get("severity"),
                "in_scope": h.get("in_scope"),
            }
            for h in (trace.get("lexicon_hits") or [])
        ],
        "tropes_fired": [t.get("trope_id") for t in (trace.get("tropes_fired") or [])],
        "trope_candidates": [
            t.get("trope_id") for t in (trace.get("trope_candidates") or [])
        ],
        # Present when the automatic flag was withheld — a term excused by its
        # never_flag_when rule, or a verdict contradicting its own category.
        "exemption": trace.get("exemption"),
        "versions": {
            "specialist_model": specialist.get("model"),
            "prompt_version": specialist.get("prompt_version"),
        },
    }

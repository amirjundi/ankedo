"""Admin chat endpoint.

Mounted behind the same bearer auth as every other router. The confirmation for a
mutating action is a second round trip: the first call returns what would change,
the client sends it back, and only then does it run.

The pending payload comes back from the client, so it is treated as untrusted —
ChatAgent.confirm re-checks the action against the registry rather than trusting
what was returned.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.chat.agent import ChatAgent
from src.core.database import session_scope

log = structlog.get_logger()
router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(default="", max_length=4000)
    # Present when the operator is confirming what a previous reply proposed.
    confirm: dict | None = None


class ChatResponse(BaseModel):
    reply: str
    pending: dict | None = None
    action_run: str | None = None


@router.post("", response_model=ChatResponse)
async def chat(request: ChatRequest, session: AsyncSession = Depends(session_scope)):
    agent = ChatAgent(session)

    if request.confirm:
        result = await agent.confirm(request.confirm)
    else:
        result = await agent.handle(request.message)

    return ChatResponse(
        reply=result.text, pending=result.pending, action_run=result.action_run
    )


@router.get("/actions")
async def list_actions():
    """What the chat can do — so the dashboard can show it without asking the model."""
    from src.chat.tools import ACTIONS

    return {
        "actions": [
            {
                "name": a.name,
                "description": a.description,
                "mutating": a.mutating,
                "arguments": a.args,
            }
            for a in ACTIONS.values()
        ]
    }

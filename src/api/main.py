"""
FastAPI entry point for the local admin and reviewer dashboard.
"""
from __future__ import annotations

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.review_endpoints import router as review_router
from src.api.accounts_router import router as accounts_router
from src.api.admin_router import router as admin_router
from src.api.evidence_router import router as evidence_router
from src.api.reports_router import router as reports_router
from src.api.notifications_router import router as notifications_router
from src.core.logging_config import configure_logging
from src.core.database import init_db, get_session_factory
import asyncio
from sqlalchemy import select
from src.models.channel_config import ChannelConfig
from src.chat.channels.telegram_channel import start_telegram_bot
import json

log = structlog.get_logger()

# Optional: Ensure logging is set up if running directly via uvicorn
try:
    configure_logging()
except Exception:
    pass


app = FastAPI(
    title="AnkEdo Admin API",
    description="Local API for reviewer dashboard and admin controls",
    version="0.1.0"
)

from src.api.auth import require_admin
from src.core.settings import get_settings

_settings = get_settings()

# `allow_origins=["*"]` with credentials enabled would let any site read the
# dashboard through a logged-in browser. Restricted to configured origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Auth is applied at inclusion rather than per-endpoint, so a new endpoint is
# protected by default. Forgetting a decorator should not silently expose data about
# people who are already targets of violence.
_protected = [Depends(require_admin)]

app.include_router(review_router, dependencies=_protected)
app.include_router(accounts_router, dependencies=_protected)
app.include_router(admin_router, dependencies=_protected)
app.include_router(evidence_router, dependencies=_protected)
app.include_router(reports_router, dependencies=_protected)
app.include_router(notifications_router, dependencies=_protected)

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    log.info("Starting up AnkEdo API")
    await init_db()
    
    # Start Telegram bot if configured
    async with get_session_factory()() as session:
        stmt = select(ChannelConfig).where(ChannelConfig.channel == "telegram", ChannelConfig.is_active == True)
        config = (await session.execute(stmt)).scalar_one_or_none()
        if config:
            try:
                from src.core.crypto import decrypt

                creds = json.loads(decrypt(config.encrypted_credentials))
                token = creds.get("token")
                chat_id = creds.get("admin_chat_id")
                if token and chat_id:
                    asyncio.create_task(start_telegram_bot(token, int(chat_id)))
            except Exception as e:
                log.error("Failed to start Telegram bot", error=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok"}

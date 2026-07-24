"""
FastAPI entry point for the local admin and reviewer dashboard.
"""
from __future__ import annotations

import structlog
from fastapi import FastAPI
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Should be locked down to localhost in production config
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(review_router)
app.include_router(accounts_router)
app.include_router(admin_router)
app.include_router(evidence_router)
app.include_router(reports_router)
app.include_router(notifications_router)

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
                # In a real system, encrypted_credentials would be decrypted here.
                # We'll assume for MVP it's stored as JSON plaintext {"token": "...", "admin_chat_id": ...}
                creds = json.loads(config.encrypted_credentials)
                token = creds.get("token")
                chat_id = creds.get("admin_chat_id")
                if token and chat_id:
                    asyncio.create_task(start_telegram_bot(token, int(chat_id)))
            except Exception as e:
                log.error("Failed to start Telegram bot", error=str(e))

@app.get("/health")
def health_check():
    return {"status": "ok"}

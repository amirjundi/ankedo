"""
FastAPI entry point for the local admin and reviewer dashboard.
"""
from __future__ import annotations

import os
from pathlib import Path

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.review_endpoints import router as review_router
from src.api.accounts_router import router as accounts_router
from src.api.admin_router import router as admin_router
from src.api.evidence_router import router as evidence_router
from src.api.reports_router import router as reports_router
from src.api.notifications_router import router as notifications_router
from src.api.chat_router import router as chat_router
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
_origins = list(_settings.cors_origins)
if _settings.extension_enabled and _settings.extension_origin:
    # The extension's service worker sends chrome-extension://<id> as its Origin.
    # Only added when the extension is both enabled and pinned to an id — a blank
    # setting must not widen CORS to every extension the operator has installed.
    _origins.append(_settings.extension_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
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
app.include_router(chat_router, dependencies=_protected)

# Optional, and off by default. Not mounted rather than mounted-and-idle: these
# endpoints accept content into the classification pipeline.
if _settings.extension_enabled:
    from src.api.extension_router import router as extension_router

    app.include_router(extension_router, dependencies=_protected)
    log.info("Extension capture endpoints enabled")

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

    # The agent itself. Without this, `ankedo start` served a dashboard and collected
    # nothing — the loop existed but only `ankedo agent run --continuous` reached it,
    # and nothing said so.
    # Never under pytest. A TestClient triggers this startup hook, and an endless
    # collection loop inside a test run hangs the suite and pollutes the database
    # every other test is asserting against.
    under_test = "PYTEST_CURRENT_TEST" in os.environ

    if _settings.run_agent_with_api and not under_test:
        app.state.agent_task = asyncio.create_task(_run_agent_loop())
        log.info("Agent loop started", interval_seconds=_settings.loop_interval_seconds)
    elif under_test:
        log.debug("Agent loop not started: running under pytest")
    else:
        log.warning("Agent loop disabled — API only (RUN_AGENT_WITH_API=false)")


async def _run_agent_loop() -> None:
    """Own session and own loop, alongside the API.

    A crash here must not take the API down with it: the dashboard is how an operator
    finds out something is wrong, so it has to outlive the thing that went wrong.
    """
    from src.core.database import get_session
    from src.core.orchestration_loop import OrchestrationLoop

    try:
        async with get_session() as session:
            await OrchestrationLoop(session).run_forever()
    except asyncio.CancelledError:
        log.info("Agent loop stopped")
        raise
    except Exception as exc:
        log.exception("Agent loop died", error=str(exc))


@app.on_event("shutdown")
async def shutdown_event():
    task = getattr(app.state, "agent_task", None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: B014 — shutdown is best effort
            pass


@app.get("/health")
def health_check():
    return {"status": "ok"}


# ── The dashboard ────────────────────────────────────────────────────────────
# `ankedo start` has always printed "Dashboard: http://host:port" and opened a
# browser there, and nothing was ever served at "/" — the React app lives in
# frontend/dist and was never mounted. The operator got a 404 from a URL the tool
# had just told them was the dashboard.
#
# Mounted last so it cannot shadow /api/*, /docs or /health.

_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


# Paths that belong to the API. A request under one of these must reach the router
# even when it does not match a route: /api/notifications has to redirect to
# /api/notifications/ and then 401, not quietly return the dashboard with 200.
# Mounting the SPA at "/" broke exactly that — a Mount matches every path beneath it,
# so unauthenticated requests to a mistyped endpoint were answered with HTML.
_API_PREFIXES = ("/api", "/docs", "/redoc", "/openapi.json", "/health")


def _is_api(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "?")
               for p in _API_PREFIXES)


if (_DIST / "index.html").exists():
    # Only the built assets are mounted. Everything else reaches the SPA through the
    # 404 handler below, which leaves routing — and slash redirects — untouched.
    if (_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    def dashboard_index():
        return FileResponse(_DIST / "index.html")

    @app.exception_handler(404)
    async def spa_fallback(request, exc):
        """Serve the app for client-side routes; leave the API's 404s alone.

        A refresh on /cases asks the server for /cases, which is not a file. Without
        this the dashboard works until someone reloads the page.
        """
        if request.method == "GET" and not _is_api(request.url.path):
            candidate = _DIST / request.url.path.lstrip("/")
            if candidate.is_file() and _DIST in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(_DIST / "index.html")
        return JSONResponse(status_code=404, content={"detail": getattr(exc, "detail", "Not Found")})

    log.info("Dashboard mounted", path=str(_DIST))
else:
    # Say which of the two situations this is, rather than 404-ing at the URL the
    # CLI just printed.
    log.warning("Dashboard not built — serving a placeholder", expected=str(_DIST))

    @app.get("/", include_in_schema=False)
    def dashboard_missing():
        return JSONResponse(
            status_code=503,
            content={
                "detail": "The dashboard has not been built.",
                "fix": "cd frontend && npm install && npm run build",
                "api_docs": "/docs",
            },
        )

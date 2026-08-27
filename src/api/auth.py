"""API authentication.

Every router shipped without any authentication, and CORS was `allow_origins=["*"]`.
On a machine holding evidence about persecuted minorities that is not a hardening
task, it is the difference between a private tool and a public one.

The user intends to expose the dashboard over the internet, likely through a
Cloudflare tunnel. **App-level auth is required regardless** — a tunnel controls who
can reach the port, not who may read the data, and treating the tunnel as the only
gate means anyone who finds the URL has everything.
"""
from __future__ import annotations

import hmac

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.core.settings import get_settings

log = structlog.get_logger()

_scheme = HTTPBearer(auto_error=False)


async def require_admin(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_scheme),
) -> str:
    """Require a valid admin token.

    Fails closed: if no token is configured, the API refuses every request rather
    than running unauthenticated. An operator who has not finished setup gets a clear
    error, not a silently open dashboard.
    """
    settings = get_settings()
    expected = settings.admin_api_token

    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ADMIN_API_TOKEN is not set. Run `ankedo token` on the agent "
                "machine to generate one, then restart the agent."
            ),
        )

    supplied = credentials.credentials if credentials else None
    if not supplied:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Constant-time: a timing side channel on a token comparison is cheap to avoid.
    if not hmac.compare_digest(supplied, expected):
        # The real client IP, not the tunnel's, so the log is worth reading.
        client = request.headers.get("cf-connecting-ip") or (
            request.client.host if request.client else "unknown"
        )
        log.warning("Rejected API request", path=request.url.path, client=client)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return "admin"

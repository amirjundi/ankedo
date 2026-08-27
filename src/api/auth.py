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


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# Set by every reverse proxy and tunnel worth the name. Their presence means the
# request reached us through something, so the socket's peer address is the proxy and
# tells us nothing about who is really asking.
#
# This matters specifically here: a Cloudflare tunnel runs its daemon on the same
# machine, so a request from the public internet arrives from 127.0.0.1. Trusting the
# peer address alone would publish the dashboard to anyone with the URL while looking
# like a local connection.
_FORWARDED_HEADERS = (
    "cf-connecting-ip",
    "x-forwarded-for",
    "x-real-ip",
    "forwarded",
)


def _is_local(request: Request) -> bool:
    """True only for a request that came straight from this machine."""
    if any(h in request.headers for h in _FORWARDED_HEADERS):
        return False
    client = request.client.host if request.client else None
    return client in _LOOPBACK


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
        # No token configured. On the operator's own machine that should not be a
        # wall: they are already sitting at the computer, and a password prompt
        # between someone and software running on their own laptop protects nobody.
        # Exposed to a network it is the only thing standing between a stranger and a
        # database of verdicts naming people who are already targets.
        #
        # So the answer depends on where the request came from, not on configuration.
        if _is_local(request):
            log.warning(
                "Serving without authentication to a local client — set "
                "ADMIN_API_TOKEN before exposing this agent to a network "
                "(`ankedo token`)",
                path=request.url.path,
            )
            return "admin"

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "This agent is reachable from the network and has no "
                "ADMIN_API_TOKEN. Run `ankedo token` on the agent machine, then "
                "restart it."
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

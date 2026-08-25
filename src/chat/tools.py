"""What the chat agent is allowed to do.

Everything the agent can perform lives in ACTIONS. A model cannot reach anything
that is not registered here — the dispatcher looks the name up in this dict and
does nothing if it misses. That is deliberate: the agent classifies text written by
strangers, so a comment it is analysing is untrusted input that reaches a model,
and the blast radius of a prompt injection is exactly the size of this file.

Two rules hold the boundary:

**Secrets are neither readable nor writable.** SETTABLE_KEYS is an allowlist, and
API keys, the admin token, the Ettok agent key and SECRET_KEY are absent from it by
construction rather than by a filter someone must remember to update. A denylist
would need editing every time a credential is added; this needs editing every time
a setting should be exposed, which is the safer direction to forget in.

**Mutations declare themselves.** An action marked mutating never runs on the
model's say-so alone — the caller confirms with a human first. Reads run freely;
the cost of a wrong read is a wasted message.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.settings import AgentSettings, get_settings

log = structlog.get_logger()


# Settings a chat message may change. Model assignments and the tuning knobs an
# operator legitimately adjusts day to day — nothing that grants access, moves data
# off the machine, or points the agent at a different platform.
SETTABLE_KEYS: dict[str, str] = {
    "TRIAGE_MODEL": "Model for the first-pass filter",
    "SPECIALIST_MODEL": "Model for deep Arabic/Kurdish analysis",
    "CRITIC_MODEL": "Model for anti-hallucination review",
    "TARGET_GROUP_MODEL": "Model that identifies the targeted group",
    "VISION_MODEL": "Model for image and video analysis",
    "CHAT_AGENT_MODEL": "Model backing this chat",
    "AUTO_FLAG_THRESHOLD": "Confidence at which an item is auto-flagged",
    "BORDERLINE_LOW": "Lower bound of the review band",
    "BORDERLINE_HIGH": "Upper bound of the review band",
    "MAX_REVIEW_BATCH_SIZE": "Items per review batch",
    "LOOP_INTERVAL_SECONDS": "Seconds between orchestration cycles",
    "MAX_POSTS_PER_ACCOUNT": "Posts collected per account per pass",
    "MAX_COMMENTS_PER_POST": "Comments collected per post",
    "LOG_LEVEL": "DEBUG, INFO, WARNING or ERROR",
}

# Named so the refusal can say why, rather than "unknown setting" — an operator who
# asks the chat to rotate a key should be told where to do it, not stonewalled.
_SECRET_KEYS = {
    "GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL",
    "ADMIN_API_TOKEN", "SECRET_KEY", "ETTOK_AGENT_KEY", "ETTOK_BASE_URL",
    "TELEGRAM_BOT_TOKEN", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET",
    "DATABASE_URL", "RESIDENTIAL_PROXY_LIST",
}


class ActionError(Exception):
    """The action could not run. The message is shown to the operator."""


@dataclass
class Action:
    name: str
    description: str
    mutating: bool
    run: Callable[..., Awaitable[str]]
    args: dict[str, str]


# ── Reads ────────────────────────────────────────────────────────────────────


async def _show_config(session: AsyncSession, **_) -> str:
    settings = get_settings()
    lines = ["Current configuration:"]
    for key, blurb in SETTABLE_KEYS.items():
        value = getattr(settings, key.lower(), None)
        lines.append(f"  {key} = {value}   ({blurb})")
    return "\n".join(lines)


async def _stats(session: AsyncSession, days: int = 7, **_) -> str:
    from src.models.case import Case
    from src.models.post import Post

    days = max(1, min(int(days), 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)

    cases = await session.scalar(select(func.count(Case.id)).where(Case.created_at >= since))
    posts = await session.scalar(select(func.count(Post.id)).where(Post.created_at >= since))
    flagged = await session.scalar(
        select(func.count(Post.id)).where(
            Post.created_at >= since, Post.hate_speech_flag.is_(True)
        )
    )
    return (
        f"Last {days} days: {posts or 0} items collected, {flagged or 0} flagged, "
        f"{cases or 0} cases opened."
    )


async def _recent_flagged(session: AsyncSession, limit: int = 10, **_) -> str:
    from src.models.post import Post

    limit = max(1, min(int(limit), 50))
    rows = (
        await session.execute(
            select(Post)
            .where(Post.hate_speech_flag.is_(True))
            .order_by(Post.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    if not rows:
        return "Nothing flagged yet."

    out = [f"{len(rows)} most recent flagged items:"]
    for post in rows:
        text = (post.content_text or "")[:110].replace("\n", " ")
        out.append(f"  [{post.created_at}] {text}")
    return "\n".join(out)


async def _health(session: AsyncSession, **_) -> str:
    from src.cli.health_check import run_checks

    checks = run_checks()
    return "\n".join(f"  {c.status.upper():5} {c.name}: {c.detail}" for c in checks)


# ── Mutations ────────────────────────────────────────────────────────────────


async def _set_config(session: AsyncSession, key: str = "", value: str = "", **_) -> str:
    key = (key or "").strip().upper()
    value = (value or "").strip()

    if not key:
        raise ActionError("No setting named.")

    if key in _SECRET_KEYS:
        raise ActionError(
            f"{key} is a credential and cannot be changed from chat. "
            f"Use `ankedo configure set {key}=...` on the machine itself."
        )

    if key not in SETTABLE_KEYS:
        raise ActionError(
            f"{key} is not adjustable from chat. Available: {', '.join(SETTABLE_KEYS)}"
        )

    if not value:
        raise ActionError(f"No value given for {key}.")

    # Validate through the settings model before writing, so a bad threshold is
    # refused here rather than crashing the next cycle that reads .env.
    try:
        AgentSettings(**{key.lower(): value})
    except Exception as exc:
        raise ActionError(f"{value!r} is not valid for {key}: {exc}") from exc

    from src.cli.setup_wizard import _load_existing_env, _write_env

    config = _load_existing_env()
    if not config:
        raise ActionError("No .env found — run `ankedo setup` first.")

    previous = config.get(key, "unset")
    config[key] = value
    _write_env(config)
    get_settings.cache_clear()

    log.info("Setting changed from chat", key=key, previous=previous, value=value)
    return f"{key}: {previous} → {value}. Restart the agent for it to take effect."


ACTIONS: dict[str, Action] = {
    a.name: a
    for a in [
        Action("show_config", "Show current configuration values", False, _show_config, {}),
        Action("stats", "Collection and flagging counts", False, _stats,
               {"days": "how many days back, default 7"}),
        Action("recent_flagged", "Most recently flagged items", False, _recent_flagged,
               {"limit": "how many, default 10, max 50"}),
        Action("health", "Run the system health checks", False, _health, {}),
        Action("set_config", "Change one configuration value", True, _set_config,
               {"key": f"one of: {', '.join(SETTABLE_KEYS)}", "value": "the new value"}),
    ]
}


def catalogue() -> str:
    """The action list, rendered for the prompt."""
    lines = []
    for action in ACTIONS.values():
        args = "; ".join(f"{k} ({v})" for k, v in action.args.items()) or "no arguments"
        mark = " [needs confirmation]" if action.mutating else ""
        lines.append(f"- {action.name}{mark}: {action.description}. Arguments: {args}")
    return "\n".join(lines)


async def run_action(name: str, session: AsyncSession, arguments: dict[str, Any]) -> str:
    action = ACTIONS.get(name)
    if action is None:
        # Not an exception the model can talk its way past: an unregistered name is
        # simply not a thing that can happen.
        raise ActionError(f"No such action: {name}")
    # Only declared arguments are forwarded, so an invented one cannot reach a query.
    allowed = {k: v for k, v in (arguments or {}).items() if k in action.args}
    return await action.run(session, **allowed)

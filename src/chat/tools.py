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

    lines = []
    for check in run_checks():
        line = f"  {check.status.upper():5} {check.name}: {check.detail}"
        # The remedy used to be dropped here, so an operator asking the agent whether
        # anything was wrong got told yes and not told what to do about it.
        if check.status != "pass" and check.fix:
            line += f"\n        → {check.fix}"
        lines.append(line)
    return "\n".join(lines)


async def _repair(session: AsyncSession, what: str = "", **_) -> str:
    """Run one named repair from the fixed registry in src/core/repairs.py."""
    from src.core.repairs import REPAIRS, RepairError, catalogue, run_repair

    what = (what or "").strip().lower()
    if not what:
        raise ActionError(f"Which repair? Available:\n{catalogue()}")
    if what not in REPAIRS:
        raise ActionError(f"No repair called {what!r}. Available:\n{catalogue()}")

    try:
        result = await run_repair(what)
    except RepairError as exc:
        raise ActionError(str(exc)) from exc

    if result.proposed:
        return f"{what} needs a human: {result.detail}"
    prefix = "Repaired" if result.ok else "Could not repair"
    return f"{prefix} {what}: {result.detail}"


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


async def _classify(session: AsyncSession, text: str = "", post_text: str = "", **_) -> str:
    """Classify a piece of text on the spot.

    The operator asked the agent to "test and report some hate speech" and it said it
    could not act at all. It could not do *that* — there was no action for it — but
    the honest answer was to offer this, which is the thing the whole system exists to
    do and takes one message to demonstrate.

    `post_text` matters more than it looks. A comment is judged against what it
    replies to, so classifying a bare phrase asks a different question from the one
    the agent normally answers. Without a parent the verdict is about the words alone.
    """
    text = (text or "").strip()
    if not text:
        raise ActionError(
            "Give me the text to classify. If it is a comment, tell me what post it "
            "was under — that usually decides the verdict."
        )

    from src.classifiers.committee.orchestrator import CommitteeOrchestrator
    from src.classifiers.context_bundle import ContextBundle
    from src.classifiers.group_resolver import GroupResolver

    parent = (post_text or "").strip()
    groups: list[str] = []
    if parent:
        # resolve_all, not resolve: the devil-worship libel is aimed at Yazidis and
        # Christians both, and a post can concern more than one community.
        groups = await GroupResolver(session).resolve_all(parent)

    result = await CommitteeOrchestrator(session).run(
        ContextBundle(
            comment_text=text,
            parent_post_text=parent,
            target_groups=groups,
        )
    )

    trace = result.get("trace") or {}
    lines = [
        f"Verdict: {result['verdict']} (confidence {result['confidence']:.2f})",
        f"Category: {result.get('category') or 'none'}   "
        f"Severity: {result.get('severity', 0)}",
    ]
    if groups:
        lines.append(f"Target group detected from the post: {groups[0]}")
    elif parent:
        lines.append("No target group detected in the post.")
    else:
        lines.append("No parent post given — judged on the words alone.")

    hits = [h.get("matched") for h in (trace.get("lexicon_hits") or [])]
    if hits:
        lines.append(f"Dictionary terms matched: {', '.join(hits)}")
    fired = [t.get("trope_id") for t in (trace.get("tropes_fired") or [])]
    if fired:
        lines.append(f"Patterns fired: {', '.join(fired)}")
    if trace.get("exemption"):
        lines.append(
            f"Automatic flag withheld: {trace['exemption']['signal']} — sent for review"
        )
    if result.get("committee_disagreement"):
        lines.append("The specialist and critic disagreed, so a human decides.")

    specialist = (trace.get("specialist") or {})
    if specialist.get("rationale"):
        lines.append(f"Reasoning: {specialist['rationale']}")

    return "\n".join(lines)


async def _test_browser(session: AsyncSession, **_) -> str:
    """Actually launch the browser and say what happened.

    Distinct from `health`, which reports a check result. This starts a browser,
    which is the only thing that answers whether collection can run at all.
    """
    from src.browsers.camoufox_worker import BrowserUnavailable, CamoufoxWorker

    # A throwaway identity: this launches a browser to see whether it launches, and
    # must not touch a worker account's saved session.
    worker = CamoufoxWorker("facebook", account_id="ankedo-browser-test")
    try:
        await worker.start()
    except BrowserUnavailable as exc:
        return (
            f"The browser will not start: {exc}\n"
            "Collection cannot run until it does. Ask me to repair the browser, or "
            "run `ankedo doctor` on the machine for the full cause."
        )
    except Exception as exc:  # noqa: BLE001 — any failure here is the answer
        return f"The browser failed to start: {type(exc).__name__}: {str(exc)[:200]}"

    try:
        return "The browser started and closed cleanly. Collection can run."
    finally:
        try:
            await worker.stop()
        except Exception:  # noqa: BLE001, S110 — a failed close is not the answer
            pass


async def _collect_now(session: AsyncSession, **_) -> str:
    """Run one collection pass immediately, instead of waiting for the next cycle."""
    from src.browsers.camoufox_worker import BrowserUnavailable
    from src.core.collection_runner import CollectionRunner

    try:
        stats = await CollectionRunner(session).run()
    except BrowserUnavailable as exc:
        raise ActionError(
            f"No browser, so nothing can be collected: {exc}. Ask me to repair the "
            "browser first."
        ) from exc

    if stats is None:
        return "The collection pass did not run."

    scanned = getattr(stats, "posts_scanned", 0)
    comments = getattr(stats, "comments_scanned", 0)
    if not scanned and not comments:
        return (
            "Collection ran and found nothing. Either no account is due yet, or no "
            "accounts are being tracked — ask me for the configuration to check."
        )
    return (
        f"Collected {scanned} posts and {comments} comments. They are queued for "
        "classification and will be judged on the next cycle."
    )


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
        Action("classify", "Judge a piece of text for hate speech right now", False,
               _classify,
               {"text": "the comment or post to judge",
                "post_text": "the post it was replying to, if it is a comment"}),
        Action("test_browser", "Launch the browser and report whether it works", False,
               _test_browser, {}),
        Action("repair", "Fix a broken tool, e.g. the browser", True, _repair,
               {"what": "browser, dependencies, directories or env_file"}),
        Action("collect_now", "Run one collection pass immediately", True,
               _collect_now, {}),
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

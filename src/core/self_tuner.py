"""Bounded self-configuration.

The agent adjusts how it *operates* and proposes changes to how it *judges*. That line
is the whole design, and it is enforced by which registry a key appears in rather than
by anything the model is told.

Why the line sits there: an agent that can raise its own `auto_flag_threshold` can
quietly stop detecting hate speech, and an agent that can rewrite its own prompts
destroys the reproducibility FR-CL-13 requires for eval-gating and audit. Neither
failure announces itself — the system keeps running and simply reports less.

Crawl pacing is the opposite case. A wrong value there costs throughput or, at worst,
an account; the feedback is fast and the blast radius is contained. That is worth
automating, because the alternative is a human watching block rates at 3am.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.settings import get_settings
from src.models.agent_config import AgentConfig, TunedBy
from src.notifications.dispatcher import NotificationDispatcher

log = structlog.get_logger()


@dataclass(frozen=True)
class Tunable:
    key: str
    default: float
    minimum: float
    maximum: float
    why: str


# The agent may change these on its own. Each is a knob where being wrong costs
# efficiency and the signal that it is wrong arrives quickly.
SELF_TUNABLE: dict[str, Tunable] = {
    t.key: t
    for t in [
        Tunable("pacing_min_delay_seconds", 2.5, 1.0, 30.0, "block rate"),
        Tunable("pacing_max_delay_seconds", 8.0, 3.0, 120.0, "block rate"),
        Tunable("max_posts_per_account", 10, 1, 50, "queue depth vs coverage"),
        Tunable("max_comments_per_post", 100, 10, 500, "queue depth vs coverage"),
        Tunable("vision_max_steps_per_task", 12, 3, 40, "vision task success rate"),
    ]
}

# The agent may only PROPOSE these. Each changes what counts as hate speech, or
# destroys the ability to reproduce a past verdict.
PROPOSAL_ONLY: dict[str, str] = {
    "auto_flag_threshold": "changes what gets flagged without a human ever seeing it",
    "borderline_low": "changes which items reach a reviewer at all",
    "borderline_high": "changes which items reach a reviewer at all",
    "daily_token_budget": "a hard cost guardrail (FR-AG-7)",
    "per_case_token_budget": "a hard cost guardrail (FR-AG-7)",
    "expansion_hate_density": "changes how aggressively the agent crawls into threads",
    "trend_zscore_threshold": "changes when the agent escalates on its own",
}


class SelfTuner:
    """Applies bounded adjustments and routes everything else to a human."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()
        self.dispatcher = NotificationDispatcher(session)

    async def current(self, key: str) -> float:
        """The live value: the tuned one if present, else the configured default."""
        row = await self._row(key)
        if row is not None:
            return row.value
        spec = SELF_TUNABLE.get(key)
        return spec.default if spec else float(getattr(self.settings, key, 0))

    async def adjust(self, key: str, value: float, reason: str) -> float:
        """Set a self-tunable key, clamped to its bounds. Returns the applied value.

        Raises PermissionError for a proposal-only key — the agent must not be able to
        reach a content decision by calling the operational path.
        """
        if key in PROPOSAL_ONLY:
            raise PermissionError(
                f"{key} is proposal-only: {PROPOSAL_ONLY[key]}. Use propose()."
            )
        spec = SELF_TUNABLE.get(key)
        if spec is None:
            raise KeyError(f"{key} is not a tunable parameter")

        clamped = max(spec.minimum, min(spec.maximum, value))
        row = await self._row(key)
        if row is None:
            row = AgentConfig(
                key=key,
                value=spec.default,
                min_value=spec.minimum,
                max_value=spec.maximum,
                default_value=spec.default,
            )
            self.session.add(row)

        if row.value == clamped:
            return clamped

        row.previous_value = row.value
        row.value = clamped
        row.tuned_by = TunedBy.AGENT
        row.reason = reason
        row.changed_at = datetime.now(timezone.utc)
        await self.session.commit()

        log.info(
            "Self-tuned", key=key, value=clamped, previous=row.previous_value, reason=reason
        )
        if clamped != value:
            log.info("Clamped to configured bounds", key=key, requested=value, applied=clamped)
        return clamped

    async def propose(self, key: str, value: float, reason: str) -> None:
        """Ask a human to change something the agent may not change itself."""
        current = await self.current(key)
        await self.dispatcher.send(
            type_="ConfigChangeProposal",
            context={
                "key": key,
                "current": current,
                "proposed": value,
                "restricted_because": PROPOSAL_ONLY.get(key, "not self-tunable"),
            },
            question=(
                f"The agent proposes changing {key} from {current} to {value}. "
                f"Reason: {reason}"
            ),
            urgency="Medium",
            suggested_actions=["Approve", "Reject", "Discuss"],
        )
        log.info("Config change proposed", key=key, proposed=value, reason=reason)

    async def revert(self, key: str) -> float | None:
        """Undo the last change to a key."""
        row = await self._row(key)
        if row is None or row.previous_value is None:
            return None

        row.value, row.previous_value = row.previous_value, row.value
        row.tuned_by = TunedBy.HUMAN
        row.reason = "reverted"
        row.changed_at = datetime.now(timezone.utc)
        await self.session.commit()
        log.info("Config reverted", key=key, value=row.value)
        return row.value

    async def history(self) -> list[AgentConfig]:
        stmt = select(AgentConfig).order_by(AgentConfig.changed_at.desc().nullslast())
        return list((await self.session.execute(stmt)).scalars().all())

    async def _row(self, key: str) -> AgentConfig | None:
        return (
            await self.session.execute(select(AgentConfig).where(AgentConfig.key == key))
        ).scalar_one_or_none()

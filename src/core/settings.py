"""
Agent settings — all configuration loaded from environment variables via pydantic-settings.
Every threshold, timeout, autonomy level, and API key is defined here.
No magic numbers elsewhere in the codebase.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    # An absolute path, not ".env". Relative, it is looked up from the process's cwd,
    # so `ankedo start` run from a home directory loaded no configuration at all —
    # every setting fell back to its default, including no API key and no admin token,
    # while `ankedo doctor` reported the file present because it resolves against the
    # project root. Silent, and only possible once `ankedo` went onto PATH.
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent.parent / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -----------------------------------------------------------------------
    # Database
    # -----------------------------------------------------------------------
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/ankedo.db",
        description="Async SQLAlchemy database URL",
    )
    data_dir: str = Field(default="./data")

    # -----------------------------------------------------------------------
    # LLM Providers
    # -----------------------------------------------------------------------
    # "gemini" or "openai". The latter covers every OpenAI-compatible endpoint —
    # OpenRouter, Groq, Together, DeepSeek, Ollama, LM Studio — via openai_base_url.
    llm_provider: str = Field(default="gemini")

    gemini_api_key: Optional[str] = Field(default=None)
    openai_api_key: Optional[str] = Field(default=None)

    # Unset means api.openai.com. Point it at any /v1 that speaks chat completions.
    openai_base_url: Optional[str] = Field(default=None)

    # Free and self-hosted models are slow and frequently rate-limited; the SDK's
    # default timeout is tuned for a paid endpoint. A run against a free proxy timed
    # out mid-classification with the model still working.
    llm_timeout_seconds: float = Field(default=180.0, gt=0)
    # One, not three. Retries multiply against the fallback chain — four retries
    # across four models is sixteen upstream calls for a single turn, and on a free
    # tier that is enough to take the endpoint down. Measured: with the aggressive
    # default the proxy fell over repeatedly; at one retry it served three passes in
    # a row and stayed up. A retry storm against a rate-limited endpoint does not
    # improve the odds, it removes them.
    llm_max_retries: int = Field(default=1, ge=0)
    # Pause before trying the next model. A rate limit needs a moment to clear;
    # asking again immediately just spends another rejection.
    fallback_delay_seconds: float = Field(default=2.0, ge=0)

    # Tried in order when the configured model is rate-limited or unavailable. On a
    # free tier a 429 is routine, not exceptional, and failing the item because one
    # model is busy loses work the agent could have done with another.
    fallback_models: str = Field(default="")

    @property
    def fallback_model_list(self) -> list[str]:
        return [m.strip() for m in self.fallback_models.split(",") if m.strip()]

    @field_validator("llm_provider")
    @classmethod
    def known_provider(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("gemini", "openai"):
            raise ValueError(f"llm_provider must be 'gemini' or 'openai', got {v!r}")
        return v

    # Model IDs are config, never hardcoded in call sites. Verify current IDs at
    # ai.google.dev/gemini-api/docs/models before changing — Gemini 2.0 and 1.5 were
    # shut down on 2026-06-01 and now return 404.
    triage_model: str = Field(default="gemini-3.5-flash-lite")
    specialist_model: str = Field(default="gemini-3.6-flash")
    critic_model: str = Field(default="gemini-3.5-flash-lite")
    vision_model: str = Field(default="gemini-3.6-flash")
    chat_agent_model: str = Field(default="gemini-3.6-flash")

    # -----------------------------------------------------------------------
    # Cost control (NFR-SC-2, FR-AG-7)
    # -----------------------------------------------------------------------
    # 0 = unlimited. A viral thread or a runaway vision loop is the real risk here,
    # not gradual overspend.
    daily_token_budget: int = Field(default=0, ge=0)
    per_case_token_budget: int = Field(default=0, ge=0)
    # Per-token USD rates. Default 0 because a wrong hardcoded price is worse than an
    # obviously absent one — set these from current Gemini pricing.
    input_token_cost_usd: float = Field(default=0.0, ge=0)
    output_token_cost_usd: float = Field(default=0.0, ge=0)

    # -----------------------------------------------------------------------
    # Agent Orchestration
    # -----------------------------------------------------------------------
    loop_interval_seconds: int = Field(default=60, ge=10)

    # Classification confidence thresholds
    auto_flag_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    borderline_low: float = Field(default=0.50, ge=0.0, le=1.0)
    borderline_high: float = Field(default=0.74, ge=0.0, le=1.0)

    # Review queue
    max_review_batch_size: int = Field(default=25, ge=1)

    # Per-pass collection caps. Bounded so one busy account cannot consume an entire
    # cycle, and so a single run's footprint stays predictable to the platform.
    max_posts_per_account: int = Field(default=10, ge=1)
    max_comments_per_post: int = Field(default=100, ge=1)

    # Reply sub-thread expansion (FR-AG-3): crawl deeper only where hate is dense,
    # since crawling every reply everywhere is what gets accounts blocked.
    expansion_hate_density: float = Field(default=0.15, ge=0.0, le=1.0)
    expansion_min_comments: int = Field(default=10, ge=1)

    # -----------------------------------------------------------------------
    # Trend detection (FR-CM-2, FR-AG-5)
    # -----------------------------------------------------------------------
    trend_baseline_days: int = Field(default=28, ge=1)
    trend_ewma_alpha: float = Field(default=0.3, gt=0, le=1)
    trend_zscore_threshold: float = Field(default=3.0, gt=0)
    # Guards against a wild rate from a handful of comments, and against calling a
    # spike before there is enough history for a baseline to mean anything.
    trend_min_sample: int = Field(default=20, ge=1)
    trend_min_history_hours: int = Field(default=24, ge=1)
    # How much faster to crawl during a spike. Bounded — FR-AG-7 does not let a spike
    # talk the agent past its rate limits.
    crawl_multiplier_on_spike: float = Field(default=3.0, ge=1.0, le=10.0)
    # Reactivating a dormant case needs a human unless the pattern is trusted (FR-AG-5).
    auto_reactivate_cases: bool = Field(default=False)
    # Collection silent for this long fires a Critical alert. A monitoring tool that
    # has stopped looks exactly like one monitoring a quiet period.
    dead_mans_switch_hours: int = Field(default=6, ge=1)

    # -----------------------------------------------------------------------
    # Autonomous Discovery
    # -----------------------------------------------------------------------
    auto_add_accounts_per_cycle: int = Field(default=5, ge=0)
    # Flagged items by one author before the agent starts watching them. Three is not
    # a coincidence and is not yet a campaign; it is enough to be worth collecting
    # more of, which is all adding to the watch list does.
    discovery_flag_threshold: int = Field(default=3, ge=1)
    # How far back to look. A long window turns someone flagged three times over a
    # year into a target; the concern is concentration, not a lifetime total.
    discovery_window_days: int = Field(default=14, ge=1)

    # -----------------------------------------------------------------------
    # API security (NFR-DP-1/2)
    # -----------------------------------------------------------------------
    # No default: the API fails closed rather than shipping a known token.
    admin_api_token: Optional[str] = Field(default=None)
    # Comma-separated origins. A tunnel controls reachability, not authorisation, so
    # this stays restrictive even when the dashboard is exposed publicly.
    cors_allowed_origins: str = Field(default="http://localhost:8000,http://127.0.0.1:8000")

    # -----------------------------------------------------------------------
    # Ettok platform (see docs/AGENT_CONTRACT.md — HTTPS only)
    # -----------------------------------------------------------------------
    ettok_base_url: str = Field(
        default="", description="Platform base URL, e.g. https://ettok.net/api/hermes/"
    )
    ettok_agent_key: Optional[str] = Field(
        default=None, description="Bearer key with the hate_speech_scan scope"
    )
    ettok_agent_id: str = Field(default="ankedo-local-01", description="Sent as X-Agent-Id")
    # Off until the platform has a verdict endpoint that stores verdicts.
    #
    # `POST flagged-items/` exists and returns 200, but it is still the original
    # prefilter: it has no columns for verdict, category, severity, confidence,
    # rationale, decided_by, versions or committee_disagreement, so every one of
    # those is silently dropped, and it re-runs its own classifier over each item —
    # the duplication the ownership reversal removed. It also writes a scan log with
    # posts_scanned set to the number of flagged items, which inverts hate density.
    #
    # A 200 that discards the payload is worse than a refusal, because nothing
    # downstream can tell. So verdicts accumulate in the outbox and wait. That is what
    # the outbox is for: the far end not being ready is exactly the case it holds work
    # through. Turn this on when the platform confirms the §7 endpoint is live, and
    # the backlog drains on the next cycle with nothing lost.
    ettok_verdict_endpoint_ready: bool = Field(default=False)
    ettok_timeout_seconds: float = Field(default=30.0, gt=0)
    ettok_max_retries: int = Field(default=3, ge=0)
    # The contract caches the lexicon per run. Allowing a stale cache keeps the agent
    # scanning through a connectivity drop on residential WiFi; set 0 to refuse to
    # scan without a fresh pull.
    lexicon_max_stale_hours: int = Field(default=24, ge=0)

    # -----------------------------------------------------------------------
    # Knowledge Packs
    # -----------------------------------------------------------------------
    default_pack_dir: str = Field(default="./packs/iraq-minorities")
    # Ordinal into the severity_levels table; the scale itself ships in the pack.
    default_case_severity: int = Field(default=2, ge=0)

    # -----------------------------------------------------------------------
    # Worker Accounts
    # -----------------------------------------------------------------------
    min_healthy_accounts_per_platform: int = Field(
        default=2, ge=0, description="Below this, fire a CapacityAlert to the admin"
    )
    # How long a blocked session waits for a human before the account is quarantined.
    # Long enough that an admin away from the machine can still act; short enough that
    # a session does not sit at a checkpoint indefinitely.

    # Follow pacing. A new account that follows 200 pages in an hour is flagged
    # immediately, so restoring coverage is spread across the warm-up period.


    # -----------------------------------------------------------------------
    # Vision browser
    # -----------------------------------------------------------------------
    # The Chrome extension is optional and off by default. Its endpoints accept
    # content into the classification pipeline, so they stay unmounted rather than
    # merely unused on an installation that does not want it — an endpoint nobody
    # knows is there is the one nobody notices being called.
    extension_enabled: bool = Field(default=False)
    # chrome-extension://<id> of the installed copy. Empty accepts any extension
    # origin, which is only appropriate while developing against an unpacked build.
    extension_origin: Optional[str] = Field(default=None)

    browser_headless: bool = Field(default=True)

    # Playwright refuses to download a browser for a distro its build registry does
    # not know yet — Ubuntu 26.04 hits this, and collection is dead until it is
    # resolved. Pointing at a browser already on the machine is the durable fix, so
    # it has to be expressible in config rather than requiring a Playwright release.
    browser_executable_path: Optional[str] = Field(
        default=None, description="Absolute path to a Chromium/Firefox binary to use"
    )
    browser_channel: Optional[str] = Field(
        default=None, description='Installed-browser channel, e.g. "chrome" or "chromium"'
    )
    vision_max_steps_per_task: int = Field(default=12, ge=1)
    # Comma-separated hostnames the vision agent may drive. Empty = unrestricted,
    # which is only appropriate in development.
    vision_domain_allowlist: str = Field(
        default="facebook.com,instagram.com,tiktok.com,m.facebook.com"
    )

    # -----------------------------------------------------------------------
    # Crawl Pacing (anti-detect)
    # -----------------------------------------------------------------------
    pacing_min_delay_seconds: float = Field(default=2.5, ge=0.5)
    pacing_max_delay_seconds: float = Field(default=8.0, ge=1.0)

    # -----------------------------------------------------------------------
    # Queue Backpressure
    # -----------------------------------------------------------------------
    classification_queue_high_water: int = Field(default=500, ge=10)

    # -----------------------------------------------------------------------
    # Case Lifecycle
    # -----------------------------------------------------------------------
    cooling_threshold_hours: int = Field(default=24, ge=1)
    dormant_threshold_days: int = Field(default=7, ge=1)

    # -----------------------------------------------------------------------
    # Learning Loop
    # -----------------------------------------------------------------------
    regression_max_drop_pp: float = Field(default=2.0, ge=0.0)
    gold_eval_min_size: int = Field(default=100, ge=10)

    # -----------------------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------------------
    notification_timeout_minutes: int = Field(default=30, ge=1)
    escalation_interval_minutes: int = Field(default=60, ge=5)

    # -----------------------------------------------------------------------
    # Telegram
    # -----------------------------------------------------------------------
    telegram_bot_token: Optional[str] = Field(default=None)
    telegram_admin_chat_id: Optional[str] = Field(default=None)

    # -----------------------------------------------------------------------
    # WhatsApp Business Cloud API
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # MCP Servers
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Security
    # -----------------------------------------------------------------------
    secret_key: Optional[str] = Field(default=None, description="32+ char key for encrypting credentials")

    # -----------------------------------------------------------------------
    # API Server
    # -----------------------------------------------------------------------
    # `ankedo start` used to bring up the API and dashboard and nothing else — the
    # orchestration loop was only reachable through `ankedo agent run --continuous`,
    # so the obvious action collected nothing, forever, with no indication. Default
    # true so "start" means start; set false to run the API alone.
    run_agent_with_api: bool = Field(default=True)

    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000, ge=1024, le=65535)

    # -----------------------------------------------------------------------
    # Evidence & Files
    # -----------------------------------------------------------------------
    evidence_dir: str = Field(default="./evidence")
    screenshot_format: str = Field(default="png")

    # -----------------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------------
    log_level: str = Field(default="INFO")
    log_dir: str = Field(default="./logs")

    # -----------------------------------------------------------------------
    # Proxy
    # -----------------------------------------------------------------------

    @field_validator("database_url")
    @classmethod
    def absolute_sqlite_path(cls, v: str) -> str:
        """Anchor a relative sqlite path to the project, not the current directory.

        The default is `sqlite+aiosqlite:///./data/ankedo.db`, which resolves against
        the process's cwd. That was invisible while the agent was only ever started
        from a checkout — then `ankedo` went onto PATH, someone ran it from their home
        directory, and startup died with "unable to open database file" while
        `ankedo doctor` reported the database present, because the doctor resolves the
        same path against the project root.

        Only relative paths are rewritten; an absolute path or a non-sqlite URL is left
        exactly as configured.
        """
        prefix = next((p for p in ("sqlite+aiosqlite:///", "sqlite:///") if v.startswith(p)), None)
        if prefix is None:
            return v

        path = v[len(prefix):]
        # A fourth slash means an absolute POSIX path; a drive letter means Windows.
        if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
            return v

        root = Path(__file__).resolve().parent.parent.parent
        resolved = (root / path).resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return f"{prefix}{resolved.as_posix()}"

    @field_validator("borderline_high")
    @classmethod
    def borderline_high_above_low(cls, v: float, info) -> float:
        low = info.data.get("borderline_low", 0.0)
        if v <= low:
            raise ValueError("borderline_high must be greater than borderline_low")
        return v



    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def vision_allowed_domains(self) -> list[str]:
        return [d.strip().lower() for d in self.vision_domain_allowlist.split(",") if d.strip()]


@lru_cache(maxsize=1)
def get_settings() -> AgentSettings:
    """Return the singleton settings instance (cached after first call)."""
    return AgentSettings()

"""
Agent settings — all configuration loaded from environment variables via pydantic-settings.
Every threshold, timeout, autonomy level, and API key is defined here.
No magic numbers elsewhere in the codebase.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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
    gemini_api_key: Optional[str] = Field(default=None)
    openai_api_key: Optional[str] = Field(default=None)
    anthropic_api_key: Optional[str] = Field(default=None)

    # Model IDs are config, never hardcoded in call sites. Verify current IDs at
    # ai.google.dev/gemini-api/docs/models before changing — Gemini 2.0 and 1.5 were
    # shut down on 2026-06-01 and now return 404.
    triage_model: str = Field(default="gemini-3.5-flash-lite")
    specialist_model: str = Field(default="gemini-3.6-flash")
    critic_model: str = Field(default="gemini-3.5-flash-lite")
    target_group_model: str = Field(default="gemini-3.5-flash-lite")
    vision_model: str = Field(default="gemini-3.6-flash")
    chat_agent_model: str = Field(default="gemini-3.6-flash")

    # -----------------------------------------------------------------------
    # Cost control (NFR-SC-2, FR-AG-7)
    # -----------------------------------------------------------------------
    # 0 = unlimited. A viral thread or a runaway vision loop is the real risk here,
    # not gradual overspend.
    daily_token_budget: int = Field(default=0, ge=0)
    per_case_token_budget: int = Field(default=0, ge=0)
    vision_daily_call_budget: int = Field(default=0, ge=0)
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
    large_network_threshold: int = Field(default=20, ge=1)

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
    handoff_timeout_minutes: int = Field(default=20, ge=1)
    handoff_poll_seconds: int = Field(default=15, ge=1)

    # Follow pacing. A new account that follows 200 pages in an hour is flagged
    # immediately, so restoring coverage is spread across the warm-up period.
    warmup_follows_per_day: int = Field(default=8, ge=1)
    active_follows_per_day: int = Field(default=20, ge=1)

    warmup_trust_threshold: int = Field(
        default=50, ge=0, le=100, description="trust_score (0-100) needed to leave WARM_UP"
    )
    recovery_idle_hours: int = Field(
        default=48, ge=1, description="Idle hours before a RECOVERY account returns to ACTIVE"
    )

    # -----------------------------------------------------------------------
    # Vision browser
    # -----------------------------------------------------------------------
    browser_headless: bool = Field(default=True)
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
    session_min_minutes: int = Field(default=20, ge=5)
    session_max_minutes: int = Field(default=90, ge=10)

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
    whatsapp_phone_number_id: Optional[str] = Field(default=None)
    whatsapp_access_token: Optional[str] = Field(default=None)
    whatsapp_app_secret: Optional[str] = Field(default=None)
    whatsapp_verify_token: Optional[str] = Field(default=None)
    whatsapp_admin_phone: Optional[str] = Field(default=None)
    whatsapp_webhook_url: Optional[str] = Field(default=None)

    # -----------------------------------------------------------------------
    # MCP Servers
    # -----------------------------------------------------------------------
    mcp_servers: str = Field(default="", description="Comma-separated list of MCP server names")
    mcp_tavily_search_url: Optional[str] = Field(default=None)
    mcp_memory_url: Optional[str] = Field(default=None)

    # -----------------------------------------------------------------------
    # Security
    # -----------------------------------------------------------------------
    secret_key: Optional[str] = Field(default=None, description="32+ char key for encrypting credentials")

    # -----------------------------------------------------------------------
    # API Server
    # -----------------------------------------------------------------------
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
    log_max_bytes: int = Field(default=10_485_760)  # 10 MB
    log_backup_count: int = Field(default=5)

    # -----------------------------------------------------------------------
    # Proxy
    # -----------------------------------------------------------------------
    residential_proxy_list: str = Field(default="", description="Comma-separated proxy URLs")

    @field_validator("borderline_high")
    @classmethod
    def borderline_high_above_low(cls, v: float, info) -> float:
        low = info.data.get("borderline_low", 0.0)
        if v <= low:
            raise ValueError("borderline_high must be greater than borderline_low")
        return v

    @property
    def mcp_server_list(self) -> list[str]:
        return [s.strip() for s in self.mcp_servers.split(",") if s.strip()]

    @property
    def proxy_list(self) -> list[str]:
        return [p.strip() for p in self.residential_proxy_list.split(",") if p.strip()]

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

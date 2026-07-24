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
    openai_api_key: Optional[str] = Field(default=None)
    anthropic_api_key: Optional[str] = Field(default=None)

    triage_model: str = Field(default="gpt-4o-mini")
    specialist_model: str = Field(default="gpt-4o")
    critic_model: str = Field(default="gpt-4o-mini")
    chat_agent_model: str = Field(default="gpt-4o-mini")

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

    # -----------------------------------------------------------------------
    # Autonomous Discovery
    # -----------------------------------------------------------------------
    auto_add_accounts_per_cycle: int = Field(default=5, ge=0)
    large_network_threshold: int = Field(default=20, ge=1)

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


@lru_cache(maxsize=1)
def get_settings() -> AgentSettings:
    """Return the singleton settings instance (cached after first call)."""
    return AgentSettings()

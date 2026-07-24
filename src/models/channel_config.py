"""Channel configuration model for encrypted API keys."""
from __future__ import annotations

from sqlalchemy import String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class ChannelConfig(Base):
    __tablename__ = "channel_configs"

    channel: Mapped[str] = mapped_column(String(50), nullable=False, unique=True) # e.g., 'telegram'
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    encrypted_credentials: Mapped[str] = mapped_column(Text, nullable=False) # JSON blob, encrypted at rest
    authorized_users: Mapped[str] = mapped_column(Text, nullable=True) # JSON list of authorized user IDs

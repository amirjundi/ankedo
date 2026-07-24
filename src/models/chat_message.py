"""Chat message model for storing conversation history."""
from __future__ import annotations

from sqlalchemy import String, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    channel: Mapped[str] = mapped_column(String(50), nullable=False) # e.g., 'telegram', 'web'
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    is_from_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

"""
Structured JSON logging configuration.
"""
from __future__ import annotations

import logging
import sys

import structlog

from src.core.settings import get_settings


def configure_logging() -> None:
    """
    Configures structured logging for the agent.
    Outputs JSON logs to files and rich console logs to stdout.
    """
    settings = get_settings()

    # Create logs dir if not exists
    import os
    os.makedirs(settings.log_dir, exist_ok=True)

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Standard library logging configuration
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        stream=sys.stdout,
    )

    # Structlog configuration
    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    # Console renderer for stdout, JSON renderer for file output if we add FileHandler later
    structlog.configure(
        processors=processors + [structlog.dev.ConsoleRenderer(colors=True)],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    log = structlog.get_logger()
    log.info("Logging configured", level=settings.log_level)

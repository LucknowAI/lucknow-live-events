"""Centralized structured logging for API and workers.

Uses structlog with contextvars for request_id / correlation_id / task_id propagation.
"""
from __future__ import annotations

import logging
import sys
import uuid
from typing import Any

import structlog

from api.core.config import settings

_configured = False


def setup_logging(*, service_name: str | None = None) -> None:
    """Configure structlog once per process. Safe to call multiple times."""
    global _configured
    if _configured:
        return

    level_name = (settings.LOG_LEVEL or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if settings.LOG_FORMAT.lower() == "console":
        processors = shared_processors + [structlog.dev.ConsoleRenderer()]
    else:
        processors = shared_processors + [structlog.processors.JSONRenderer()]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    service = service_name or settings.SERVICE_NAME
    context: dict[str, Any] = {"environment": settings.ENVIRONMENT}
    if service:
        context["service"] = service
    structlog.contextvars.bind_contextvars(**context)

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger. Ensures logging is configured."""
    setup_logging()
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """Bind key-value pairs to the current async/task context."""
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear contextvars after a request or Celery task completes."""
    structlog.contextvars.clear_contextvars()


def get_correlation_id() -> str | None:
    """Return the active correlation ID, if any."""
    return structlog.contextvars.get_contextvars().get("correlation_id")


def new_correlation_id() -> str:
    """Generate and bind a fresh correlation ID."""
    correlation_id = str(uuid.uuid4())
    bind_context(correlation_id=correlation_id, request_id=correlation_id)
    return correlation_id

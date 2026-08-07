"""One logger for the whole engine.

Two shapes from one config: pretty console for local work, and GCP-structured JSON in
cloud (`severity` + `message` are the field names Cloud Logging actually indexes; using
structlog's default `level`/`event` means every log line shows up as INFO with no
message).

A redaction processor runs before rendering. It is not a substitute for not logging
secrets — it is the backstop for the day someone logs a whole request body.
"""

from __future__ import annotations

import logging
import re
import sys
import uuid
from typing import Any

import structlog

from v1.config.settings import V1Settings, get_settings

REDACTED = "***redacted***"

# Field names whose values are never safe to emit, at any nesting depth.
SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "password",
        "secret",
        "token",
        "engine_token",
        "x-engine-token",
        "cookie",
        "set-cookie",
    }
)

# Value shapes that are a key regardless of what field they arrived in. Ordered longest
# prefix first so a more specific pattern wins.
SECRET_VALUE_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"),  # OpenRouter
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),  # Groq
    re.compile(r"AIza[0-9A-Za-z\-_]{20,}"),  # Google API keys
    re.compile(r"sk-[A-Za-z0-9]{20,}"),  # OpenAI-style
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{16,}=*", re.IGNORECASE),
)

_MAX_STRING = 4096

_configured = False


def _scrub(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return value
    if isinstance(value, str):
        scrubbed = value
        for pattern in SECRET_VALUE_PATTERNS:
            scrubbed = pattern.sub(REDACTED, scrubbed)
        if len(scrubbed) > _MAX_STRING:
            scrubbed = f"{scrubbed[:_MAX_STRING]}…[truncated {len(scrubbed)} chars]"
        return scrubbed
    if isinstance(value, dict):
        return {
            key: (
                REDACTED
                if isinstance(key, str) and key.lower() in SENSITIVE_KEYS
                else _scrub(item, depth=depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub(item, depth=depth + 1) for item in value)
    return value


def redact_processor(_logger: Any, _name: str, event_dict: dict) -> dict:
    """structlog processor: remove secret-shaped values from every field."""
    return _scrub(event_dict)


def gcp_rename_processor(_logger: Any, _name: str, event_dict: dict) -> dict:
    """Rename structlog's defaults to the fields Cloud Logging indexes."""
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    if "level" in event_dict:
        event_dict["severity"] = event_dict.pop("level").upper()
    return event_dict


def setup_logging(settings: V1Settings | None = None, *, force: bool = False) -> None:
    """Configure structlog for this process. Idempotent unless `force=True`."""
    global _configured
    if _configured and not force:
        return

    settings = settings or get_settings()
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_processor,
    ]

    if settings.LOG_FORMAT.lower() == "console":
        processors = [*shared, structlog.dev.ConsoleRenderer()]
    else:
        processors = [*shared, gcp_rename_processor, structlog.processors.JSONRenderer()]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        service=settings.SERVICE_NAME, environment=settings.ENVIRONMENT
    )
    _configured = True


def get_logger(name: str | None = None) -> Any:
    """Bound logger. Configures logging on first use so no module needs to remember to."""
    setup_logging()
    return structlog.get_logger(name)


def bind(**kwargs: Any) -> None:
    structlog.contextvars.bind_contextvars(**kwargs)


def clear() -> None:
    structlog.contextvars.clear_contextvars()


def correlation_id() -> str | None:
    return structlog.contextvars.get_contextvars().get("correlation_id")


def new_correlation_id() -> str:
    value = str(uuid.uuid4())
    bind(correlation_id=value)
    return value

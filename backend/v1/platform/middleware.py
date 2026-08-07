"""Request middleware: correlation id in, one structured line out.

Every log line emitted while handling a request carries the same `correlation_id`, which is
also returned in the `X-Correlation-Id` response header. An inbound `X-Correlation-Id` is
honoured so a caller (or a later job runner) can stitch a trace across processes.

A failing request logs the exception with its full stack trace and re-raises. It is never
swallowed — the exception handlers in `v1.api.app` turn typed engine errors into the right
status code, and anything else into a 500 that is loud in the logs.
"""

from __future__ import annotations

import re
from time import perf_counter

from starlette.requests import Request
from starlette.types import ASGIApp

from v1.platform.logging import bind, clear, get_logger, new_correlation_id

logger = get_logger("v1.http")

HEADER = "X-Correlation-Id"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")

# Health checks would otherwise dominate the log volume with no information.
QUIET_PATHS = frozenset({"/health", "/metrics"})


class CorrelationLoggingMiddleware:
    """Pure ASGI middleware so it also sees exceptions raised by other middleware."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        inbound = request.headers.get(HEADER)
        correlation = inbound if inbound and _SAFE_ID.match(inbound) else None
        clear()
        correlation_id = correlation or new_correlation_id()
        if correlation:
            bind(correlation_id=correlation)

        started = perf_counter()
        status_holder: dict[str, int] = {}

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((HEADER.lower().encode(), correlation_id.encode()))
            await send(message)

        path = scope.get("path", "")
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception(
                "request_failed",
                method=scope.get("method"),
                path=path,
                duration_ms=int((perf_counter() - started) * 1000),
            )
            raise
        else:
            if path not in QUIET_PATHS:
                logger.info(
                    "request",
                    method=scope.get("method"),
                    path=path,
                    status=status_holder.get("status"),
                    duration_ms=int((perf_counter() - started) * 1000),
                )
        finally:
            clear()


__all__ = ["HEADER", "CorrelationLoggingMiddleware"]

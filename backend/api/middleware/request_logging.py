"""HTTP request logging middleware for FastAPI."""
from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from api.core.logging import bind_context, clear_context, get_logger

log = get_logger(__name__)

# Paths logged at debug to avoid noise (e.g. keep-alive health pings every 30s).
_QUIET_PATHS = frozenset({"/health", "/favicon.ico"})


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        bind_context(
            request_id=request_id,
            correlation_id=request_id,
            http_method=request.method,
            http_path=request.url.path,
        )

        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            fields = {
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
                "client_ip": request.client.host if request.client else None,
            }
            if request.url.path in _QUIET_PATHS:
                log.debug("http.request", **fields)
            else:
                log.info("http.request", **fields)
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log.exception(
                "http.request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
                client_ip=request.client.host if request.client else None,
            )
            raise
        finally:
            clear_context()

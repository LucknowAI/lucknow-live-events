"""The only module that makes network calls."""

import asyncio
import math
import time

import httpx
import structlog

log = structlog.get_logger(__name__)

USER_AGENT = "LucknowLiveEvents (+https://github.com/LucknowAI/lucknow-live-events)"
# 20s for each phase (connect, read, write, pool), not one budget for the whole request.
TIMEOUT = 20.0
_MAX_RETRIES = 2
_BACKOFF_S = (1.0, 2.0)
_MAX_RETRY_AFTER_S = 60.0

# ponytail: one in-flight request process-wide. Fine for sequential fetching.
# Upgrade: per-host limit if a later issue fetches sources concurrently.
_lock = asyncio.Lock()
_client: httpx.AsyncClient | None = None


def make_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
        transport=transport,
    )


def _shared_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = make_client()
    return _client


async def close() -> None:
    """Close the shared client. The runner calls this once, when the run ends."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _delay(response: httpx.Response, attempt: int) -> float:
    if response.status_code == 429:
        raw = response.headers.get("Retry-After", "")
        try:
            delay = float(raw)
        except ValueError:
            delay = math.nan
        if math.isfinite(delay):
            return min(max(delay, 0.0), _MAX_RETRY_AFTER_S)
    return _BACKOFF_S[attempt]


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


async def get(
    url: str,
    *,
    params: dict[str, str | int | float] | None = None,
    client: httpx.AsyncClient | None = None,
    log_url: bool = True,
) -> httpx.Response:
    """GET `url`. Two retries (three tries) on 429, 5xx, timeouts and dropped connections.

    Any other non-2xx response raises immediately. A numeric `Retry-After` on a
    429 is honored, capped at 60s. The URL is logged unless `log_url=False`; never
    pass secrets in a URL that is logged.
    """
    http = client or _shared_client()
    requested = _request_url(url, params)
    logged_url = requested if log_url else "<redacted>"
    async with _lock:
        for attempt in range(_MAX_RETRIES + 1):
            started = time.perf_counter()
            try:
                response = await http.get(url, params=params)
            except (httpx.TimeoutException, httpx.NetworkError):
                log.info("http.request", url=logged_url, status=None, duration_ms=_ms(started))
                if attempt == _MAX_RETRIES:
                    raise
                await asyncio.sleep(_BACKOFF_S[attempt])
                continue
            log.info(
                "http.request",
                url=str(response.url) if log_url else "<redacted>",
                status=response.status_code,
                duration_ms=_ms(started),
            )
            if _retryable(response.status_code) and attempt < _MAX_RETRIES:
                await response.aclose()
                await asyncio.sleep(_delay(response, attempt))
                continue
            response.raise_for_status()
            return response


def _request_url(url: str, params: dict[str, str | int | float] | None) -> str:
    return str(httpx.Request("GET", url, params=params).url)


def _ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)

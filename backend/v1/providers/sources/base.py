"""Shared HTTP plumbing for source enumerators.

Enumerators talk to other people's servers. Three things belong here rather than in each
adapter, for the same reason the LLM funnel exists: if every adapter decided its own
timeout, retry policy and size cap, "add a source" would mean "re-argue the safety rules".

* **Typed errors from status codes.** 429 → `RateLimited`, 5xx/connect → `ProviderUnavailable`,
  401/403 → `ProviderAuthError`, timeout → `ProviderTimeout`. The failover and retry logic
  above only works if adapters agree on what a failure *is*.
* **A hard byte cap, enforced while streaming.** `len(response.content)` after the fact is
  not a cap — by then the bytes are already in memory. The body is read in chunks and the
  read aborts the moment the cap is passed.
* **An identifying User-Agent.** We are a polite crawler with a name, not an anonymous
  bot. Configurable, because the contact URL is deployment-specific.
* **Redirects followed by hand, re-validating every hop.** Letting the HTTP client follow
  them means the server we are talking to picks our next destination, which defeats the
  address guard entirely — see `BaseSourceEnumerator.client`.

Politeness (`robots.txt`, `Crawl-delay`, per-host connection limits) is Phase 3's fetch
module. Enumeration touches a handful of well-known endpoints per run, not a crawl
frontier, so the Phase 3 machinery would be premature here — but it is a real gap and is
named as one rather than left implied.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from v1 import __version__
from v1.config.schema import SourceConfig
from v1.contracts.discovery import DiscoveredItem
from v1.contracts.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
    SourceEnumerationFailed,
)
from v1.contracts.source import SourceKind, SourceTier
from v1.platform.logging import get_logger
from v1.platform.net import UnsafeUrl, assert_fetchable

logger = get_logger("v1.sources")

DEFAULT_USER_AGENT = f"engine-v1/{__version__} (+ingestion; contact via repository)"

MAX_REDIRECTS = 5
"""Hops followed before giving up. Each one is re-validated against the address guard —
see `BaseSourceEnumerator.client` for why the HTTP client is not allowed to follow them
itself."""

MAX_RESPONSE_BYTES = 25 * 1024 * 1024
"""25 MB. The sitemap protocol caps an uncompressed sitemap at 50 MB, but nothing we
enumerate comes close — Commudle's 5,058-URL events sitemap is well under 1 MB. A cap
that is generous enough to never bite a legitimate source and tight enough to stop a
runaway is the useful shape."""


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, ProviderError) and exc.retryable


def map_status(status: int, *, url: str, body_excerpt: str = "") -> ProviderError:
    """One status→error mapping for every source adapter."""
    context: dict[str, Any] = {"url": url, "status": status}
    if body_excerpt:
        context["body"] = body_excerpt[:300]

    if status == 429:
        return RateLimited(f"rate limited by {url}", **context)
    if status in {401, 403}:
        return ProviderAuthError(f"not authorised for {url} (HTTP {status})", **context)
    if status == 408 or status == 504:
        return ProviderTimeout(f"upstream timeout from {url} (HTTP {status})", **context)
    if status >= 500:
        return ProviderUnavailable(f"{url} returned HTTP {status}", **context)
    return ProviderError(f"{url} returned HTTP {status}", **context)


class BaseSourceEnumerator(ABC):
    """Implements the `SourceEnumerator` port; subclasses implement `_enumerate` only."""

    adapter: str
    kind: SourceKind = SourceKind.URL_LIST

    def __init__(self, *, source: SourceConfig, user_agent: str = DEFAULT_USER_AGENT) -> None:
        self.source = source
        self.source_id = source.id
        self.tier: SourceTier = source.tier
        self._user_agent = user_agent
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ subclass API

    @abstractmethod
    async def _enumerate(self) -> list[DiscoveredItem]:
        """Do the source-specific work. May raise the typed errors from `contracts.errors`."""

    # ---------------------------------------------------------------------- the port

    async def enumerate_items(self) -> list[DiscoveredItem]:
        items = await self._enumerate()
        if len(items) > self.source.max_items:
            # Truncation is a decision, so it is logged as one. Silently returning the
            # first N would make a source that outgrew its cap look like a source that
            # shrank.
            logger.warning(
                "source_items_truncated",
                source=self.source_id,
                adapter=self.adapter,
                returned=len(items),
                max_items=self.source.max_items,
            )
            items = items[: self.source.max_items]
        logger.info(
            "source_enumerated",
            source=self.source_id,
            adapter=self.adapter,
            tier=str(self.tier),
            items=len(items),
        )
        return items

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------------ http

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.source.timeout_s),
                headers={"User-Agent": self._user_agent},
                # Redirects are followed BY HAND (`_next_hop`), never by the client.
                #
                # Automatic following defeats the address guard completely: the guard runs
                # once, against the URL we chose, and then the server we are talking to
                # gets to pick every subsequent destination. `302 Location:
                # http://169.254.169.254/latest/meta-data/` is a two-line attack, and a
                # sitemap index is *already* third-party input telling us what to fetch.
                # Every hop has to be checked, so the client must not hop on its own.
                follow_redirects=False,
            )
        return self._client

    async def get_bytes(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> bytes:
        """One GET with the shared safety rules, retries, size cap and typed errors."""
        try:
            assert_fetchable(url)
        except UnsafeUrl as exc:
            raise SourceEnumerationFailed(
                f"source {self.source_id!r} refused to fetch {url}: {exc}",
                source=self.source_id,
                url=url,
            ) from exc

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential_jitter(initial=0.5, max=8.0, jitter=0.5),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                return await self._get_once(
                    url, params=params, headers=headers, max_bytes=max_bytes
                )
        raise AssertionError("unreachable: AsyncRetrying always returns or raises")

    async def get_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        raw = await self.get_bytes(
            url, params=params, headers={"Accept": "application/json", **(headers or {})}
        )
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SourceEnumerationFailed(
                f"{url} did not return valid JSON: {exc}",
                source=self.source_id,
                url=url,
            ) from exc

    async def _get_once(
        self,
        url: str,
        *,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        max_bytes: int,
    ) -> bytes:
        """One logical GET, following redirects by hand with a guard on every hop."""
        current = url
        for hop in range(MAX_REDIRECTS + 1):
            try:
                request = self.client.build_request("GET", current, params=params, headers=headers)
                response = await self.client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise ProviderTimeout(f"timeout fetching {current}", url=current) from exc
            except httpx.HTTPError as exc:
                raise ProviderUnavailable(f"could not reach {current}: {exc}", url=current) from exc

            try:
                if response.is_redirect:
                    current = self._next_hop(response, current, hop)
                    # Query parameters belong to the URL we chose, not to wherever the
                    # server points us; re-appending them to a redirect target is how a
                    # filter silently comes off.
                    params = None
                    continue

                if response.status_code >= 400:
                    excerpt = ""
                    try:
                        excerpt = (await response.aread()).decode("utf-8", "replace")
                    except httpx.HTTPError:  # pragma: no cover - body already gone
                        pass
                    raise map_status(response.status_code, url=current, body_excerpt=excerpt)
                return await self._read_capped(response, current, max_bytes)
            finally:
                await response.aclose()

        raise SourceEnumerationFailed(
            f"{url} exceeded {MAX_REDIRECTS} redirects",
            source=self.source_id,
            url=url,
            limit=MAX_REDIRECTS,
        )

    def _next_hop(self, response: httpx.Response, current: str, hop: int) -> str:
        """Resolve and re-validate a redirect target. See `client` for why this is manual."""
        location = response.headers.get("location")
        if not location:
            raise SourceEnumerationFailed(
                f"{current} returned HTTP {response.status_code} with no Location header",
                source=self.source_id,
                url=current,
            )

        # Relative Locations are legal and common, so resolve against the current URL
        # before validating — validating the raw header would pass anything relative.
        target = str(httpx.URL(current).join(location))
        try:
            assert_fetchable(target)
        except UnsafeUrl as exc:
            raise SourceEnumerationFailed(
                f"source {self.source_id!r} refused a redirect from {current} to {target}: {exc}",
                source=self.source_id,
                url=current,
                redirect_to=target,
            ) from exc

        logger.info(
            "source_redirect_followed",
            source=self.source_id,
            hop=hop + 1,
            from_url=current,
            to_url=target,
            status=response.status_code,
        )
        return target

    @staticmethod
    async def _read_capped(response: httpx.Response, url: str, max_bytes: int) -> bytes:
        """Stream the body, aborting the moment it exceeds the cap.

        The naive version — read everything, then check `len` — is not a cap at all: the
        allocation has already happened by the time the check runs.
        """
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise SourceEnumerationFailed(
                    f"{url} exceeded the {max_bytes} byte response cap",
                    url=url,
                    limit_bytes=max_bytes,
                )
            chunks.append(chunk)
        return b"".join(chunks)

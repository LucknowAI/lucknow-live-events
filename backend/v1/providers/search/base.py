"""The shared call funnel every search adapter runs through.

An adapter implements exactly one thing — `_search`: take a prepared query, put it on the
wire, hand back normalised `SerpResult`s plus whatever credit figure the vendor reported.
Everything that affects *what the engine discovers and what it spends* happens here
instead, once:

    num guard → capability check → cache lookup ─hit→ return (free)
              → live-spend gate → budget check
              → invoke (transport retries) → cache store
              → ledger row (always, in `finally`)

Same argument as the LLM funnel. If each adapter had its own caching and retry policy,
"fail over to DataForSEO" would silently change which URLs the pipeline sees — and
discovery decides what eventually gets published.

Invariants enforced here, not by convention:

* **`num > 10` is refused** unless `search.allow_deep_pages` is explicitly on. Serper
  charges one credit for up to 10 results and two for 11–100, so two pages of ten cost
  less than one page of a hundred and return the same useful top twenty.
* **A page the provider cannot serve is never requested.** Tavily has no pagination
  parameter; asking anyway pays for page 1 again and the novelty floor reads the repeat as
  "this query is mined out".
* **An operator-dependent query is never sent to a provider that ignores operators.**
  It comes back full of plausible results for a query that was never really run.
* **Every query writes a ledger row** — successes, failures, budget blocks and cache hits.
  A failed paid query still cost money, and an unmeasured cache is folklore.
* **A paid provider is not called unless the process opted in to live spend.** A resolved
  API key is not consent; see `_assert_live_spend_permitted`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from time import perf_counter
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from v1.config.schema import SearchProviderConfig
from v1.contracts.errors import (
    BudgetExceeded,
    LiveSpendNotPermitted,
    ProviderAuthError,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
    SearchUnsupported,
)
from v1.contracts.search import (
    DEFAULT_NUM,
    SearchCapabilities,
    SearchOutcome,
    SearchQuery,
    SearchResponse,
    SerpResult,
)
from v1.platform.budget import SearchBudgetGuard, SearchSpendRecord
from v1.platform.logging import correlation_id, get_logger
from v1.providers.search.cache import NullSerpCache, SerpCache

logger = get_logger("v1.search")

USER_AGENT_HEADER = "engine-v1"


@dataclass(frozen=True, slots=True)
class RawSearch:
    """What an adapter returns from one wire call. Adapters do no caching or accounting."""

    results: list[SerpResult]
    credits: int | None = None
    """Provider-reported credit consumption, when the vendor reports one."""

    raw: dict[str, Any] | None = None
    """The untouched response body, kept so a fixture recording is a real recording."""


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, ProviderError) and exc.retryable


class BaseSearchProvider(ABC):
    """Implements the `SearchProvider` port; subclasses implement `_search` only."""

    adapter: str

    def __init__(
        self,
        *,
        name: str,
        config: SearchProviderConfig,
        api_key: str | None = None,
        budget: SearchBudgetGuard | None = None,
        cache: SerpCache | None = None,
        allow_deep_pages: bool = False,
        allow_live: bool = False,
    ) -> None:
        self.name = name
        self.config = config
        self.capabilities: SearchCapabilities = config.capabilities
        self._api_key = api_key
        self._budget = budget
        self._cache: SerpCache = cache if cache is not None else NullSerpCache()
        self._allow_deep_pages = allow_deep_pages
        self._allow_live = allow_live
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------ subclass API

    @abstractmethod
    async def _search(self, query: SearchQuery) -> RawSearch:
        """Put one prepared query on the wire and normalise the organic results.

        Must raise the typed `ProviderError` subclasses — `RateLimited` for 429/quota,
        `ProviderTimeout` for timeouts, `ProviderUnavailable` for 5xx and connection
        failures, `ProviderAuthError` for auth — so the funnel and the failover chain can
        tell what is worth retrying and what is worth abandoning.
        """

    # ---------------------------------------------------------------------- the port

    async def search(self, query: SearchQuery) -> SearchResponse:
        started = perf_counter()
        outcome = SearchOutcome.PROVIDER_ERROR
        error_type: str | None = None
        error_message: str | None = None
        results: list[SerpResult] = []
        credits: int | None = None
        cached = False
        cost = Decimal(0)

        try:
            self._assert_servable(query)

            # The cache is consulted *before* the spend gate and the budget check on
            # purpose: a cached
            # response costs nothing, so refusing to serve one because the cap is reached
            # would withhold free data for no benefit. The cap exists to stop spending,
            # not to stop working.
            key = query.cache_key(self.name)
            hit = await self._cache.get(key)
            if hit is not None:
                cached = True
                outcome = SearchOutcome.CACHE_HIT
                # Re-stamped with *this* query's template id: two templates can render to
                # the same string, and the result should be traceable to the one that
                # asked for it, not to whichever one populated the cache.
                results = [
                    result.model_copy(update={"template_id": query.template_id})
                    for result in hit.results
                ]
                return hit.model_copy(
                    update={
                        "query": query,
                        "results": results,
                        "cached": True,
                        "outcome": SearchOutcome.CACHE_HIT,
                        "cost_usd": Decimal(0),
                        "latency_ms": int((perf_counter() - started) * 1000),
                    }
                )

            self._assert_live_spend_permitted()

            if self._budget is not None and self.config.is_paid:
                await self._budget.check(provider=self.name)

            raw = await self._invoke_with_retries(query)
            results = raw.results
            credits = raw.credits
            cost = self._price(query)
            outcome = SearchOutcome.OK

            response = SearchResponse(
                provider=self.name,
                adapter=self.adapter,
                query=query,
                results=results,
                outcome=outcome,
                credits=credits,
                cost_usd=cost,
                cost_is_estimated=True,
                latency_ms=int((perf_counter() - started) * 1000),
                cached=False,
            )
            await self._cache.put(key, response)
            return response

        except SearchUnsupported as exc:
            outcome = SearchOutcome.UNSUPPORTED
            error_type, error_message = exc.code, exc.message
            raise
        except LiveSpendNotPermitted as exc:
            outcome = SearchOutcome.SPEND_NOT_PERMITTED
            error_type, error_message = exc.code, exc.message
            raise
        except BudgetExceeded as exc:
            outcome = SearchOutcome.BUDGET_BLOCKED
            error_type, error_message = exc.code, exc.message
            raise
        except Exception as exc:
            outcome = SearchOutcome.PROVIDER_ERROR
            error_type = getattr(exc, "code", type(exc).__name__)
            error_message = str(exc)[:2000]
            raise
        finally:
            await self._record(
                query=query,
                results_count=len(results),
                credits=credits,
                cost=cost,
                latency_ms=int((perf_counter() - started) * 1000),
                cached=cached,
                outcome=outcome,
                error_type=error_type,
                error_message=error_message,
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ----------------------------------------------------------------- funnel pieces

    def _assert_live_spend_permitted(self) -> None:
        """Refuse to spend real money unless this process explicitly opted in.

        A resolved API key is **not** consent. Keys live in shared `.env` files and are
        inherited by every shell, test runner and container on the machine, and a single
        discovery run issues hundreds of queries with nobody watching. So spending needs a
        second switch that nothing sets by accident (`V1_SEARCH_ALLOW_LIVE`).

        This is checked *after* the cache lookup — a cached response costs nothing, so
        serving one is fine either way — and *before* the budget check, because "this
        process may not spend at all" is a stronger statement than "the cap is reached".
        """
        if self.config.is_costless or self._allow_live:
            return
        raise LiveSpendNotPermitted(
            f"search provider {self.name!r} ({self.adapter}) costs money and "
            "V1_SEARCH_ALLOW_LIVE is not set. A configured API key is not consent to "
            "spend it. Set V1_SEARCH_ALLOW_LIVE=1 to permit live queries, or run with "
            "V1_SEARCH_FORCE_PROVIDER pointing at a fixture provider",
            provider=self.name,
            adapter=self.adapter,
        )

    def _assert_servable(self, query: SearchQuery) -> None:
        """Refuse a query this provider cannot honour, loudly, before spending on it."""
        if query.num > DEFAULT_NUM and not self._allow_deep_pages:
            raise SearchUnsupported(
                f"num={query.num} exceeds {DEFAULT_NUM} and search.allow_deep_pages is "
                "false. Serper bills 11–100 results as two credits, so two pages of ten "
                "cost less than one page of a hundred",
                provider=self.name,
                num=query.num,
            )
        if query.num > self.capabilities.max_num:
            raise SearchUnsupported(
                f"provider {self.name!r} returns at most {self.capabilities.max_num} "
                f"results per query; num={query.num} would be silently truncated",
                provider=self.name,
                num=query.num,
                max_num=self.capabilities.max_num,
            )
        if query.page > 1 and not self.capabilities.supports_pagination:
            raise SearchUnsupported(
                f"provider {self.name!r} has no pagination parameter, so page "
                f"{query.page} cannot be requested. Serving page 1 instead would be paid "
                "for and would read as 'no novel URLs'",
                provider=self.name,
                page=query.page,
            )
        if query.requires_operators and not self.capabilities.supports_operators:
            raise SearchUnsupported(
                f"query requires Google operator syntax and provider {self.name!r} does "
                "not support it; the results would look plausible for a query that was "
                "never really run",
                provider=self.name,
                template_id=query.template_id,
            )

    async def _invoke_with_retries(self, query: SearchQuery) -> RawSearch:
        """Transport-level retries only — rate limits, timeouts and 5xx.

        Auth errors are not retried (a bad key stays bad, and retrying it delays the
        failover that would actually have worked), and neither is `SearchUnsupported`,
        which is a permanent property of the provider rather than a transient failure.
        """
        attempt_number = 0
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.config.max_retries + 1),
            wait=wait_exponential_jitter(initial=0.5, max=8.0, jitter=0.5),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                attempt_number += 1
                if attempt_number > 1:
                    logger.info(
                        "search_transport_retry",
                        provider=self.name,
                        adapter=self.adapter,
                        attempt=attempt_number,
                    )
                return await self._search(query)
        raise AssertionError("unreachable: AsyncRetrying always returns or raises")

    def _price(self, query: SearchQuery) -> Decimal:
        """Cost from config, with the deep-query multiplier applied.

        Prices carry a `source` and an `as_of` date and config validation refuses a paid
        provider without them, so there is no path where a paid query is priced at zero by
        omission.
        """
        pricing = self.config.pricing
        if pricing is None:
            return Decimal(0)
        cost = pricing.usd_per_query
        if query.num > DEFAULT_NUM:
            cost *= pricing.deep_query_multiplier
        return Decimal(cost).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    async def _record(
        self,
        *,
        query: SearchQuery,
        results_count: int,
        credits: int | None,
        cost: Decimal,
        latency_ms: int,
        cached: bool,
        outcome: SearchOutcome,
        error_type: str | None,
        error_message: str | None,
    ) -> None:
        logger.info(
            "search_call",
            provider=self.name,
            adapter=self.adapter,
            template_id=query.template_id,
            query_hash=query.query_hash,
            page=query.page,
            num=query.num,
            results=results_count,
            credits=credits,
            cost_usd=str(cost),
            latency_ms=latency_ms,
            cached=cached,
            outcome=str(outcome),
            error_type=error_type,
        )
        if self._budget is None:
            return
        await self._budget.record(
            SearchSpendRecord(
                provider=self.name,
                adapter=self.adapter,
                query=query.q,
                query_hash=query.query_hash,
                template_id=query.template_id,
                page=query.page,
                num=query.num,
                results_count=results_count,
                credits=credits,
                cost_usd=cost,
                cost_is_estimated=True,
                latency_ms=latency_ms,
                cached=cached,
                outcome=outcome,
                error_type=error_type,
                error_message=error_message,
                correlation_id=correlation_id(),
            )
        )

    # ------------------------------------------------------------------------- http

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.timeout_s),
                headers={"User-Agent": USER_AGENT_HEADER},
            )
        return self._client

    async def post_json(
        self, url: str, *, json_body: Any, headers: dict[str, str] | None = None
    ) -> Any:
        """POST with typed errors. Shared because every SERP vendor is a JSON POST."""
        try:
            response = await self.client.post(url, json=json_body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"timeout calling {self.name}", provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"could not reach {self.name}: {exc}", provider=self.name
            ) from exc
        return self._decode(response, url)

    async def get_json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        try:
            response = await self.client.get(url, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"timeout calling {self.name}", provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"could not reach {self.name}: {exc}", provider=self.name
            ) from exc
        return self._decode(response, url)

    def _decode(self, response: httpx.Response, url: str) -> Any:
        if response.status_code >= 400:
            raise self.map_status(response.status_code, response.text)
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(
                f"{self.name} returned a non-JSON body from {url}",
                provider=self.name,
                body=response.text[:300],
            ) from exc

    def map_status(self, status: int, body: str = "") -> ProviderError:
        """Status→error mapping shared by every adapter, overridable where a vendor differs.

        **403 is an auth error here, not a "forbidden, maybe retry".** Verified 2026-08-08:
        Serper answers both a missing and an invalid key with `403 {"message":
        "Unauthorized.", "statusCode": 403}`. The habitual mapping — 401 auth, 403
        something else — would retry a permanently bad key with backoff on every query in
        the run and delay the failover that would actually have worked.
        """
        context: dict[str, Any] = {"provider": self.name, "status": status}
        if body:
            context["body"] = body[:300]
        if status == 429:
            return RateLimited(f"{self.name} rate limited the request", **context)
        if status in {401, 403}:
            return ProviderAuthError(f"{self.name} rejected the API key", **context)
        if status in {408, 504}:
            return ProviderTimeout(f"{self.name} timed out upstream", **context)
        if status >= 500:
            return ProviderUnavailable(f"{self.name} returned HTTP {status}", **context)
        return ProviderError(f"{self.name} returned HTTP {status}", **context)

"""The search funnel, the failover chain, and the three provider adapters on the wire.

The funnel tests are the ones that matter most: they check the invariants that stop a
provider swap from quietly changing what the engine discovers and what it spends.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from v1.config.schema import (
    OnSearchBudgetExceeded,
    SearchProviderConfig,
)
from v1.contracts.errors import (
    AllProvidersExhausted,
    BudgetExceeded,
    ProviderAuthError,
    ProviderUnavailable,
    RateLimited,
    SearchUnsupported,
)
from v1.contracts.search import SearchOutcome, SearchQuery, SerpResult
from v1.platform.budget import (
    InMemorySearchSpendLedger,
    SearchBudgetGuard,
    SearchSpendRecord,
)
from v1.platform.quota import SearchFailoverChain
from v1.providers.search.base import BaseSearchProvider, RawSearch
from v1.providers.search.cache import InMemorySerpCache
from v1.providers.search.dataforseo import DataForSeoProvider
from v1.providers.search.serper import SerperProvider
from v1.providers.search.tavily import TavilyProvider

PRICING = {
    "usd_per_query": 0.001,
    "deep_query_multiplier": 2,
    "source": "test",
    "as_of": "2026-08-08",
}


def _provider_config(adapter: str, **overrides: object) -> SearchProviderConfig:
    payload: dict = {"adapter": adapter, "api_key_ref": "TEST_KEY", "pricing": PRICING}
    payload.update(overrides)
    return SearchProviderConfig.model_validate(payload)


def _guard(cap: str = "5.00", ledger: InMemorySearchSpendLedger | None = None) -> SearchBudgetGuard:
    return SearchBudgetGuard(
        ledger=ledger or InMemorySearchSpendLedger(),
        monthly_cap_usd=Decimal(cap),
        on_exceeded=OnSearchBudgetExceeded.HALT,
        timezone="Asia/Kolkata",
    )


class StubProvider(BaseSearchProvider):
    """A provider whose wire call is scripted. Exercises the funnel, not a vendor."""

    adapter = "stub"

    def __init__(
        self, *, results: list[SerpResult] | None = None, error: Exception | None = None, **kwargs
    ) -> None:
        # Defaults to opted-in, because almost every test here is about what the funnel
        # does *once* it is allowed to spend. The gate itself has its own tests, which
        # pass allow_live explicitly.
        kwargs.setdefault("allow_live", True)
        super().__init__(**kwargs)
        self._results = results or []
        self._error = error
        self.calls = 0

    async def _search(self, query: SearchQuery) -> RawSearch:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return RawSearch(results=list(self._results), credits=1)


def _result(index: int = 1) -> SerpResult:
    return SerpResult(url=f"https://example.com/e/{index}", title="Event", position=index, page=1)


# ------------------------------------------------------------------ funnel rules


async def test_num_above_ten_is_refused_without_an_opt_in() -> None:
    """Serper bills 11-100 results as two credits, so two pages of ten cost less."""
    provider = StubProvider(
        name="p", config=_provider_config("serper"), budget=_guard(), allow_deep_pages=False
    )
    with pytest.raises(SearchUnsupported, match="allow_deep_pages"):
        await provider.search(SearchQuery(q="x", num=50))
    assert provider.calls == 0


async def test_num_above_the_provider_ceiling_is_refused() -> None:
    """Tavily caps `max_results` at 20; a silently truncated page reads as low novelty."""
    provider = StubProvider(
        name="p", config=_provider_config("tavily"), budget=_guard(), allow_deep_pages=True
    )
    with pytest.raises(SearchUnsupported, match="at most 20"):
        await provider.search(SearchQuery(q="x", num=50))


async def test_page_two_is_refused_on_a_provider_without_pagination() -> None:
    """Tavily has no page parameter at all. Asking anyway pays for page 1 again and the
    novelty floor reads the repeat as 'this query is mined out'."""
    provider = StubProvider(name="p", config=_provider_config("tavily"), budget=_guard())
    with pytest.raises(SearchUnsupported, match="no pagination parameter"):
        await provider.search(SearchQuery(q="x", page=2))
    assert provider.calls == 0


async def test_operator_query_is_refused_on_a_provider_that_ignores_operators() -> None:
    provider = StubProvider(name="p", config=_provider_config("tavily"), budget=_guard())
    with pytest.raises(SearchUnsupported, match="operator syntax"):
        await provider.search(SearchQuery(q="site:x.com y", requires_operators=True))


async def test_every_query_writes_a_ledger_row_including_failures() -> None:
    ledger = InMemorySearchSpendLedger()
    provider = StubProvider(
        name="p",
        config=_provider_config("serper"),
        budget=_guard(ledger=ledger),
        allow_live=True,
        error=ProviderUnavailable("boom"),
    )
    with pytest.raises(ProviderUnavailable):
        await provider.search(SearchQuery(q="x"))

    assert len(ledger.entries) == 1
    assert ledger.entries[0].outcome is SearchOutcome.PROVIDER_ERROR


async def test_cache_hit_is_free_and_still_recorded() -> None:
    """An unmeasured cache is folklore; the whole point of the SERP cache is that tier-A
    templates repeat every run."""
    ledger = InMemorySearchSpendLedger()
    cache = InMemorySerpCache(ttl_hours=1)
    provider = StubProvider(
        name="p",
        config=_provider_config("serper"),
        budget=_guard(ledger=ledger),
        allow_live=True,
        cache=cache,
        results=[_result()],
    )
    query = SearchQuery(q="repeat me")

    first = await provider.search(query)
    second = await provider.search(query)

    assert provider.calls == 1
    assert first.cost_usd > 0
    assert second.cached is True
    assert second.cost_usd == Decimal(0)
    assert second.result_count == 1
    assert [entry.outcome for entry in ledger.entries] == [
        SearchOutcome.OK,
        SearchOutcome.CACHE_HIT,
    ]


async def test_cache_is_served_even_when_over_budget() -> None:
    """The cap exists to stop spending, not to stop working."""
    ledger = InMemorySearchSpendLedger()
    cache = InMemorySerpCache(ttl_hours=1)
    guard = _guard(cap="0.005", ledger=ledger)
    provider = StubProvider(
        name="p", config=_provider_config("serper"), budget=guard, cache=cache, results=[_result()]
    )
    query = SearchQuery(q="cached")
    await provider.search(query)

    await ledger.record(
        SearchSpendRecord(
            provider="p",
            adapter="stub",
            query="other",
            query_hash="h",
            page=1,
            num=10,
            results_count=0,
            credits=None,
            cost_usd=Decimal("10.00"),
            cost_is_estimated=True,
            latency_ms=1,
            cached=False,
            outcome=SearchOutcome.OK,
            at=datetime.now(UTC),
        )
    )
    guard._cached_spend = None

    assert (await provider.search(query)).cached is True

    with pytest.raises(BudgetExceeded):
        await provider.search(SearchQuery(q="not cached"))


async def test_deep_query_multiplier_is_applied() -> None:
    provider = StubProvider(
        name="p", config=_provider_config("serper"), budget=_guard(), allow_deep_pages=True
    )
    cheap = await provider.search(SearchQuery(q="a", num=10))
    deep = await provider.search(SearchQuery(q="b", num=50))
    assert deep.cost_usd == cheap.cost_usd * 2


# ---------------------------------------------------------------- failover chain


async def test_rate_limit_advances_to_the_next_provider() -> None:
    first = StubProvider(
        name="first", config=_provider_config("serper"), budget=_guard(), error=RateLimited("429")
    )
    second = StubProvider(
        name="second", config=_provider_config("serper"), budget=_guard(), results=[_result()]
    )
    chain = SearchFailoverChain(providers=[first, second], consecutive_failures=2)

    response = await chain.search(SearchQuery(q="x"))

    assert response.provider == "second"
    assert chain.drain_switches() == ["first→second: provider_rate_limited"]


async def test_auth_error_opens_the_breaker_immediately() -> None:
    """A bad key stays bad; retrying it delays the failover that would have worked."""
    first = StubProvider(
        name="first",
        config=_provider_config("serper"),
        budget=_guard(),
        allow_live=True,
        error=ProviderAuthError("bad key"),
    )
    second = StubProvider(
        name="second", config=_provider_config("serper"), budget=_guard(), results=[_result()]
    )
    chain = SearchFailoverChain(providers=[first, second], consecutive_failures=99)

    await chain.search(SearchQuery(q="x"))
    assert not chain.health["first"].is_available()

    await chain.search(SearchQuery(q="y"))
    assert first.calls == 1  # never retried after the breaker opened


async def test_unsupported_does_not_count_against_provider_health() -> None:
    """Asking Tavily for page 2 is our mistake, not Tavily's."""
    first = StubProvider(name="first", config=_provider_config("tavily"), budget=_guard())
    second = StubProvider(
        name="second", config=_provider_config("serper"), budget=_guard(), results=[_result()]
    )
    chain = SearchFailoverChain(providers=[first, second], consecutive_failures=1)

    await chain.search(SearchQuery(q="x", page=3))

    assert chain.health["first"].consecutive_failures == 0
    assert chain.health["first"].is_available()


async def test_budget_exceeded_never_fails_over_to_another_paid_provider() -> None:
    """That is spending *around* the cap, not under it — the same defect class as the LLM
    layer's `degrade_profile` rule."""
    first = StubProvider(
        name="first",
        config=_provider_config("serper"),
        budget=_guard(),
        allow_live=True,
        error=BudgetExceeded("cap", spent_usd=6.0, cap_usd=5.0, window="month"),
    )
    second = StubProvider(
        name="second", config=_provider_config("serper"), budget=_guard(), results=[_result()]
    )
    chain = SearchFailoverChain(providers=[first, second])

    with pytest.raises(AllProvidersExhausted) as excinfo:
        await chain.search(SearchQuery(q="x"))

    assert second.calls == 0
    assert excinfo.value.context["budget_blocked"] is True


async def test_budget_exceeded_may_fall_through_to_a_costless_provider() -> None:
    first = StubProvider(
        name="first",
        config=_provider_config("serper"),
        budget=_guard(),
        allow_live=True,
        error=BudgetExceeded("cap", spent_usd=6.0, cap_usd=5.0, window="month"),
    )
    replay = StubProvider(
        name="replay",
        config=SearchProviderConfig.model_validate({"adapter": "fixture"}),
        results=[_result()],
    )
    chain = SearchFailoverChain(providers=[first, replay])

    assert (await chain.search(SearchQuery(q="x"))).provider == "replay"


async def test_exhausted_chain_reports_every_attempt() -> None:
    providers = [
        StubProvider(
            name=f"p{index}",
            config=_provider_config("serper"),
            budget=_guard(),
            allow_live=True,
            error=ProviderUnavailable("down"),
        )
        for index in range(2)
    ]
    chain = SearchFailoverChain(providers=providers)

    with pytest.raises(AllProvidersExhausted) as excinfo:
        await chain.search(SearchQuery(q="x"))

    assert set(excinfo.value.context["attempts"]) == {"p0", "p1"}


# --------------------------------------------------------------- adapters (wire)


@respx.mock
async def test_serper_request_body_and_parsing() -> None:
    route = respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "searchParameters": {"q": "x"},
                "organic": [
                    {
                        "title": "GDG DevFest",
                        "link": "https://gdg.community.dev/events/details/devfest/",
                        "snippet": "A community event",
                        "position": 1,
                        "date": "Sep 4, 2026",
                    },
                    {"title": "no link"},
                ],
                "credits": 1,
            },
        )
    )
    provider = SerperProvider(
        name="serper", config=_provider_config("serper"), api_key="k", allow_live=True
    )
    response = await provider.search(SearchQuery(q="x", page=2, template_id="alpha"))
    await provider.aclose()

    body = route.calls[0].request
    assert body.headers["x-api-key"] == "k"
    import json as _json

    payload = _json.loads(body.content)
    assert payload["page"] == 2
    assert payload["num"] == 10
    # Autocorrect would silently rewrite a dork built from exact names and `inurl:`
    # fragments — and we would be billed for the rewritten query.
    assert payload["autocorrect"] is False

    assert response.result_count == 1
    assert response.results[0].published_at == "Sep 4, 2026"
    assert response.results[0].template_id == "alpha"
    assert response.credits == 1


@respx.mock
async def test_serper_403_is_a_hard_auth_error_not_a_retry() -> None:
    """Verified live 2026-08-08: Serper answers a bad key with 403, not 401."""
    route = respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(403, json={"message": "Unauthorized.", "statusCode": 403})
    )
    provider = SerperProvider(
        name="serper", config=_provider_config("serper"), api_key="bad", allow_live=True
    )
    with pytest.raises(ProviderAuthError):
        await provider.search(SearchQuery(q="x"))
    await provider.aclose()
    assert route.call_count == 1  # never retried


@respx.mock
async def test_tavily_caps_max_results_and_reads_reported_credits() -> None:
    route = respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(
            200,
            json={
                "query": "x",
                "results": [
                    {
                        "title": "T",
                        "url": "https://example.com/e/1",
                        "content": "body",
                        "score": 0.9,
                    }
                ],
                "usage": {"credits": 1},
            },
        )
    )
    provider = TavilyProvider(
        name="tavily",
        config=_provider_config("tavily", max_num=20),
        api_key="tvly-k",
        allow_live=True,
    )
    response = await provider.search(SearchQuery(q="x", num=10))
    await provider.aclose()

    import json as _json

    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer tvly-k"
    assert _json.loads(request.content)["max_results"] == 10
    assert response.credits == 1
    assert response.results[0].snippet == "body"


@respx.mock
async def test_dataforseo_rejects_an_error_reported_inside_a_200_body() -> None:
    """The finding this adapter exists for: HTTP status is not the whole story, and a
    failed task read as an empty result set is indistinguishable from 'nothing new today'."""
    respx.post("https://api.dataforseo.com/v3/serp/google/organic/live/advanced").mock(
        return_value=httpx.Response(
            200,
            json={
                "status_code": 40100,
                "status_message": "You are not authorized to access this resource.",
                "tasks": None,
            },
        )
    )
    provider = DataForSeoProvider(
        name="dfs",
        config=_provider_config("dataforseo", mode="live"),
        api_key="login:password",
        allow_live=True,
    )
    with pytest.raises(ProviderAuthError, match="envelope status 40100"):
        await provider.search(SearchQuery(q="x"))
    await provider.aclose()


@respx.mock
async def test_dataforseo_rejects_a_task_level_error_under_a_20000_envelope() -> None:
    respx.post("https://api.dataforseo.com/v3/serp/google/organic/live/advanced").mock(
        return_value=httpx.Response(
            200,
            json={
                "status_code": 20000,
                "status_message": "Ok.",
                "tasks": [{"id": "t", "status_code": 40501, "status_message": "Invalid Field."}],
            },
        )
    )
    provider = DataForSeoProvider(
        name="dfs",
        config=_provider_config("dataforseo", mode="live"),
        api_key="login:password",
        allow_live=True,
    )
    with pytest.raises(Exception, match="task status 40501"):
        await provider.search(SearchQuery(q="x"))
    await provider.aclose()


@respx.mock
async def test_dataforseo_parses_organic_items_for_the_requested_page() -> None:
    respx.post("https://api.dataforseo.com/v3/serp/google/organic/live/advanced").mock(
        return_value=httpx.Response(
            200,
            json={
                "status_code": 20000,
                "status_message": "Ok.",
                "cost": 0.002,
                "tasks": [
                    {
                        "id": "t",
                        "status_code": 20000,
                        "result": [
                            {
                                "items": [
                                    {
                                        "type": "organic",
                                        "rank_absolute": 1,
                                        "page": 1,
                                        "title": "Page one hit",
                                        "url": "https://example.com/e/1",
                                        "description": "d",
                                    },
                                    {
                                        "type": "organic",
                                        "rank_absolute": 12,
                                        "page": 2,
                                        "title": "Page two hit",
                                        "url": "https://example.com/e/2",
                                    },
                                    {"type": "paid", "url": "https://ad.example.com"},
                                ]
                            }
                        ],
                    }
                ],
            },
        )
    )
    provider = DataForSeoProvider(
        name="dfs",
        config=_provider_config("dataforseo", mode="live"),
        api_key="login:password",
        allow_live=True,
    )
    response = await provider.search(SearchQuery(q="x", page=2))
    await provider.aclose()

    # `depth` returns everything up to the requested page, so page 2 is a slice — and ads
    # are never organic results.
    assert [result.url for result in response.results] == ["https://example.com/e/2"]


async def test_dataforseo_requires_a_login_password_credential() -> None:
    provider = DataForSeoProvider(
        name="dfs",
        config=_provider_config("dataforseo", mode="live"),
        api_key="just-a-token",
        allow_live=True,
    )
    with pytest.raises(ProviderAuthError, match="login:password"):
        await provider.search(SearchQuery(q="x"))
    await provider.aclose()

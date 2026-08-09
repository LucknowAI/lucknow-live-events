"""Regressions for the defects found in the Phase 2 review (2026-08-09).

One test per finding, each named for the thing that was actually wrong rather than for the
code that changed — so if a refactor reintroduces the behaviour, the failure says what
broke rather than which function moved.
"""

from __future__ import annotations

import gzip
from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest
import respx

from v1.config.schema import (
    AdapterKind,
    LLMProfileConfig,
    OnBudgetExceeded,
    OnSearchBudgetExceeded,
    PricingConfig,
    SearchProviderConfig,
    SourceConfig,
)
from v1.contracts.errors import (
    AllProvidersExhausted,
    LiveSpendNotPermitted,
    MalformedFeed,
    SourceEnumerationFailed,
)
from v1.contracts.llm import CallOutcome
from v1.contracts.search import SearchOutcome, SearchQuery, SerpResult
from v1.contracts.source import SourceTier
from v1.platform.budget import (
    BudgetGuard,
    InMemorySearchSpendLedger,
    InMemorySpendLedger,
    SearchBudgetGuard,
    SearchSpendRecord,
    SpendRecord,
)
from v1.platform.net import UnsafeUrl, assert_fetchable, is_fetchable
from v1.platform.quota import SearchFailoverChain
from v1.providers.llm.mock import MockProvider
from v1.providers.search.base import BaseSearchProvider, RawSearch
from v1.providers.sources.sitemap import SitemapEnumerator, parse_sitemap

SITEMAP_URL = "https://feeds.example.com/sitemap.xml"


# ============================================================ H2 — address literals


@pytest.mark.parametrize(
    "host",
    [
        # Every one of these resolves to 127.0.0.1 through the system resolver, and every
        # one is rejected by `ipaddress.ip_address` — so a "not an IP, must be a hostname"
        # check lets all four straight through.
        "2130706433",  # 32-bit integer
        "0177.0.0.1",  # octal first octet
        "127.1",  # short form
        "0x7f000001",  # hexadecimal
    ],
)
def test_legacy_numeric_ip_encodings_are_refused(host: str) -> None:
    with pytest.raises(UnsafeUrl, match="neither a valid IP literal nor a plausible DNS name"):
        assert_fetchable(f"http://{host}/latest/meta-data/")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:5432/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped: not loopback *as IPv6*
        "http://100.64.0.1/",  # carrier-grade NAT
        "http://0.0.0.0/",
        "http://metadata.google.internal/",
        "file:///etc/passwd",
        "gopher://example.com/",
    ],
)
def test_unfetchable_targets_stay_unfetchable(url: str) -> None:
    assert not is_fetchable(url)


@pytest.mark.parametrize(
    "url",
    ["https://gdg.community.dev/api/event/", "https://json.commudle.com/x.xml", "http://a.co/b"],
)
def test_ordinary_hosts_are_still_allowed(url: str) -> None:
    """The guard must not be so strict that it blocks the sources we actually use."""
    assert is_fetchable(url)


# ================================================================= H1 — redirects


def _sitemap_source(**overrides: object) -> SourceConfig:
    return SourceConfig.model_validate(
        {"id": "feed", "adapter": "sitemap", "tier": SourceTier.T1_FEED, "url": SITEMAP_URL}
        | overrides
    )


_URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://site.example.com/events/one</loc></url>
</urlset>"""


@respx.mock
async def test_a_redirect_to_a_private_address_is_refused() -> None:
    """The guard ran once, then `follow_redirects=True` handed destination choice to the
    server. Two lines of `Location` were enough to reach the metadata endpoint."""
    respx.get(SITEMAP_URL).mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
    )
    metadata = respx.get("http://169.254.169.254/latest/").mock(
        return_value=httpx.Response(200, content=b"secrets")
    )

    enumerator = SitemapEnumerator(source=_sitemap_source())
    with pytest.raises(SourceEnumerationFailed, match="refused a redirect"):
        await enumerator.enumerate_items()
    await enumerator.aclose()

    assert metadata.call_count == 0


@respx.mock
async def test_a_legitimate_redirect_is_still_followed() -> None:
    respx.get(SITEMAP_URL).mock(
        return_value=httpx.Response(301, headers={"location": "/sitemap-v2.xml"})
    )
    respx.get("https://feeds.example.com/sitemap-v2.xml").mock(
        return_value=httpx.Response(200, content=_URLSET)
    )

    enumerator = SitemapEnumerator(source=_sitemap_source())
    items = await enumerator.enumerate_items()
    await enumerator.aclose()

    assert [item.url for item in items] == ["https://site.example.com/events/one"]


@respx.mock
async def test_a_redirect_loop_terminates() -> None:
    respx.get(SITEMAP_URL).mock(return_value=httpx.Response(302, headers={"location": SITEMAP_URL}))
    enumerator = SitemapEnumerator(source=_sitemap_source())
    with pytest.raises(SourceEnumerationFailed, match="exceeded 5 redirects"):
        await enumerator.enumerate_items()
    await enumerator.aclose()


# =================================================================== H3 — DOCTYPE


def test_a_padded_entity_bomb_is_refused() -> None:
    """The scan used to cover only the first 4 KB — a window the attacker controls."""
    bomb = (
        b'<?xml version="1.0"?>\n'
        + b"<!-- "
        + b"A" * 8192
        + b" -->\n"
        + b'<!DOCTYPE lolz [ <!ENTITY lol "lol"> '
        + b'<!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;"> ]>\n'
        + b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + b"<url><loc>&lol1;</loc></url></urlset>"
    )
    assert len(bomb) > 4096
    with pytest.raises(MalformedFeed, match="DOCTYPE"):
        parse_sitemap(bomb, url=SITEMAP_URL)


def test_a_gzipped_padded_bomb_is_refused_after_decompression() -> None:
    """Compression is another way to push the DTD past a prefix scan."""
    bomb = b"<!-- " + b"B" * 9000 + b" -->\n<!DOCTYPE x []><urlset/>"
    with pytest.raises(MalformedFeed, match="DOCTYPE"):
        parse_sitemap(gzip.compress(bomb), url=SITEMAP_URL)


# ============================================================== H4 — spend opt-in

PRICING = PricingConfig(
    input_usd_per_mtok=Decimal("0.25"),
    output_usd_per_mtok=Decimal("1.50"),
    source="test",
    as_of=datetime.now(UTC).date(),
)
SEARCH_PRICING = {
    "usd_per_query": 0.001,
    "source": "test",
    "as_of": "2026-08-09",
}


class _Stub(BaseSearchProvider):
    adapter = "stub"

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.calls = 0

    async def _search(self, query: SearchQuery) -> RawSearch:
        self.calls += 1
        return RawSearch(results=[SerpResult(url="https://e.example/1", position=1, page=1)])


def _paid_search_config() -> SearchProviderConfig:
    return SearchProviderConfig.model_validate(
        {"adapter": "serper", "api_key_ref": "K", "pricing": SEARCH_PRICING}
    )


def _search_guard() -> SearchBudgetGuard:
    return SearchBudgetGuard(
        ledger=InMemorySearchSpendLedger(),
        monthly_cap_usd=Decimal("5.00"),
        on_exceeded=OnSearchBudgetExceeded.HALT,
        timezone="Asia/Kolkata",
    )


async def test_paid_search_refuses_to_spend_without_the_opt_in() -> None:
    """A resolved API key is not consent. This was documented and not enforced."""
    ledger = InMemorySearchSpendLedger()
    guard = SearchBudgetGuard(
        ledger=ledger,
        monthly_cap_usd=Decimal("5.00"),
        on_exceeded=OnSearchBudgetExceeded.HALT,
        timezone="Asia/Kolkata",
    )
    provider = _Stub(name="p", config=_paid_search_config(), budget=guard, allow_live=False)

    with pytest.raises(LiveSpendNotPermitted, match="V1_SEARCH_ALLOW_LIVE"):
        await provider.search(SearchQuery(q="x"))

    assert provider.calls == 0
    # Still ledgered, so a blocked attempt is visible rather than invisible.
    assert ledger.entries[0].outcome is SearchOutcome.SPEND_NOT_PERMITTED


async def test_costless_search_never_needs_the_opt_in() -> None:
    provider = _Stub(
        name="replay",
        config=SearchProviderConfig.model_validate({"adapter": "fixture"}),
        allow_live=False,
    )
    assert (await provider.search(SearchQuery(q="x"))).result_count == 1


async def test_spend_block_does_not_fall_through_to_another_paid_provider() -> None:
    """Same rule as the budget cap: the constraint is on the process, so the next paid
    provider is the same money under a different name."""
    first = _Stub(name="first", config=_paid_search_config(), budget=_search_guard())
    second = _Stub(name="second", config=_paid_search_config(), budget=_search_guard())
    chain = SearchFailoverChain(providers=[first, second])

    with pytest.raises(AllProvidersExhausted) as excinfo:
        await chain.search(SearchQuery(q="x"))

    assert second.calls == 0
    assert excinfo.value.context["spend_blocked"] is True
    # Not a fault — a healthy provider must not be benched for our configuration.
    assert chain.health["first"].consecutive_failures == 0


async def test_spend_block_may_fall_through_to_a_costless_provider() -> None:
    paid = _Stub(name="paid", config=_paid_search_config(), budget=_search_guard())
    replay = _Stub(
        name="replay", config=SearchProviderConfig.model_validate({"adapter": "fixture"})
    )
    chain = SearchFailoverChain(providers=[paid, replay])
    assert (await chain.search(SearchQuery(q="x"))).provider == "replay"


async def test_paid_llm_refuses_to_spend_without_the_opt_in() -> None:
    """The same gap existed on the LLM funnel; the review only spotted it on search."""
    from v1.providers.llm.schemas import LlmSelfTestProbe

    ledger = InMemorySpendLedger()
    guard = BudgetGuard(
        ledger=ledger,
        daily_cap_usd=Decimal("2.00"),
        on_exceeded=OnBudgetExceeded.HALT,
        timezone="Asia/Kolkata",
    )
    provider = MockProvider(
        name="paid",
        profile=LLMProfileConfig(
            adapter=AdapterKind.GEMINI_NATIVE,
            model="gemini-3.1-flash-lite",
            api_key_ref="GEMINI_API_KEY",
            pricing=PRICING,
        ),
        budget=guard,
        allow_live=False,
    )

    with pytest.raises(LiveSpendNotPermitted, match="V1_LLM_ALLOW_LIVE"):
        await provider.complete_structured(
            system="s", user="u", schema=LlmSelfTestProbe, task="extraction"
        )

    assert ledger.entries[0].outcome is CallOutcome.SPEND_NOT_PERMITTED


# ========================================================= A2 — budget fails closed


class _BrokenLedger(InMemorySearchSpendLedger):
    """A ledger whose writes fail, the way a database under pressure does."""

    def __init__(self) -> None:
        super().__init__()
        self._unrecorded = Decimal(0)

    async def record(self, entry: SearchSpendRecord) -> None:
        self._unrecorded += entry.cost_usd  # swallowed write, carried forward

    @property
    def unrecorded_usd(self) -> Decimal:
        return self._unrecorded


async def test_cap_still_trips_when_ledger_writes_are_failing() -> None:
    """The failure mode: writes fail silently, the guard's cache expires, the re-read
    cannot see rows that were never written, spend appears to fall to zero, and the
    breaker stops breaking exactly during an incident."""
    ledger = _BrokenLedger()
    guard = SearchBudgetGuard(
        ledger=ledger,
        monthly_cap_usd=Decimal("0.01"),
        on_exceeded=OnSearchBudgetExceeded.HALT,
        timezone="Asia/Kolkata",
        cache_ttl_s=0.0,  # force a re-read on every check
    )

    await guard.record(
        SearchSpendRecord(
            provider="p",
            adapter="stub",
            query="q",
            query_hash="h",
            page=1,
            num=10,
            results_count=0,
            credits=None,
            cost_usd=Decimal("5.00"),
            cost_is_estimated=True,
            latency_ms=1,
            cached=False,
            outcome=SearchOutcome.OK,
        )
    )

    status = await guard.status()
    assert status.spent_usd >= Decimal("5.00")
    assert str(status.verdict) == "halt"


async def test_llm_cap_also_counts_unrecorded_spend() -> None:
    class _BrokenLLMLedger(InMemorySpendLedger):
        def __init__(self) -> None:
            super().__init__()
            self._unrecorded = Decimal(0)

        async def record(self, entry: SpendRecord) -> None:
            self._unrecorded += entry.cost_usd

        @property
        def unrecorded_usd(self) -> Decimal:
            return self._unrecorded

    ledger = _BrokenLLMLedger()
    guard = BudgetGuard(
        ledger=ledger,
        daily_cap_usd=Decimal("0.01"),
        on_exceeded=OnBudgetExceeded.HALT,
        timezone="Asia/Kolkata",
        cache_ttl_s=0.0,
    )
    await guard.record(
        SpendRecord(
            task="extraction",
            profile="p",
            adapter="a",
            model="m",
            schema_name="S",
            strategy="native_schema",
            tokens_in=1,
            tokens_out=1,
            cost_usd=Decimal("3.00"),
            cost_is_estimated=False,
            latency_ms=1,
            attempts=1,
            outcome=CallOutcome.OK,
        )
    )
    assert (await guard.status()).spent_usd >= Decimal("3.00")


# ================================================== A4 — breaker outlives a request


def test_the_failover_chain_is_reused_so_the_breaker_has_memory(
    settings, write_discovery_config, search_resolver
) -> None:
    """A chain rebuilt per request starts every run with a clean bill of health: it
    re-attempts a known-bad key, waits out its timeouts again, and `cooldown_minutes`
    means nothing. `GET /discovery/providers` would also always report zero failures."""
    from v1.config.loader import load_config
    from v1.providers.search.registry import SearchRegistry

    loaded = load_config(write_discovery_config(), settings=settings, resolver=search_resolver)
    registry = SearchRegistry.from_config(loaded, settings=settings)
    assert registry is not None

    first = registry.build_chain()
    first.health["primary"].record_failure("provider_unavailable")

    second = registry.build_chain()
    assert second is first
    assert second.health["primary"].consecutive_failures == 1

    # And `/discovery/providers` reads that same live state without being handed the chain.
    info = {entry.name: entry for entry in registry.providers()}
    assert info["primary"].consecutive_failures == 1


# ================================================ A5 — cursor identity vs. attribution


async def test_cursor_is_keyed_per_query_not_on_the_first_chain_link() -> None:
    """Keying on `chain[0]` records progress under a provider that may never have served
    a page. The depth belongs to the query; who answered is recorded separately."""
    from v1.modules.discovery.store import CHAIN_SCOPE, InMemoryDiscoveryStore, QueryCursor

    store = InMemoryDiscoveryStore()
    cursor = await store.get_cursor(template_id="alpha", query_hash="h1", provider=CHAIN_SCOPE)
    cursor.last_provider = "dataforseo"  # failed over away from the first link
    cursor.next_page = 3
    await store.save_cursor(cursor)

    # A later run reads the same cursor without knowing which provider will serve it.
    again: QueryCursor = await store.get_cursor(
        template_id="alpha", query_hash="h1", provider=CHAIN_SCOPE
    )
    assert again.next_page == 3
    assert again.last_provider == "dataforseo"


# ================================================= A3 — preview reads, but writes not


async def test_preview_reads_real_novelty_while_writing_nothing() -> None:
    """Preview on a blank store wrote nothing (right) but also read nothing (wrong), so
    novelty was always 1.0 and the tuning surface said every template was productive."""
    from v1.contracts.discovery import DiscoveredItem, StrategyKind
    from v1.modules.discovery.store import (
        CHAIN_SCOPE,
        InMemoryDiscoveryStore,
        ReadThroughDiscoveryStore,
    )
    from v1.modules.discovery.urls import url_hash

    seen_url = "https://example.com/events/details/already-known"
    durable = InMemoryDiscoveryStore(seen={url_hash(seen_url)})
    preview = ReadThroughDiscoveryStore(durable)

    fresh = "https://example.com/events/details/brand-new"
    novel = await preview.novel_hashes([url_hash(seen_url), url_hash(fresh)])

    # The already-seen URL is correctly reported as not novel — the whole point.
    assert novel == {url_hash(fresh)}

    written = await preview.record_items(
        [
            DiscoveredItem(
                url=fresh,
                raw_url=fresh,
                strategy=StrategyKind.GENERAL,
                origin="alpha",
                tier=SourceTier.T2_STATIC,
            )
        ]
    )
    assert written == 0
    assert preview.skipped_writes == 1
    # And the underlying state is genuinely untouched.
    assert await durable.novel_hashes([url_hash(fresh)]) == {url_hash(fresh)}

    cursor = await preview.get_cursor(template_id="a", query_hash="h", provider=CHAIN_SCOPE)
    cursor.next_page = 9
    await preview.save_cursor(cursor)
    assert (
        await durable.get_cursor(template_id="a", query_hash="h", provider=CHAIN_SCOPE)
    ).next_page == 1


# ============================================ Bevy — rows must be on the source's host


@respx.mock
async def test_bevy_rows_pointing_off_host_are_dropped() -> None:
    """Per-row *chapter* verification is impossible — the API omits `chapter` from every
    response even when it is explicitly requested. Host attribution is what it does
    support, and a row pointing elsewhere means the payload is not what we asked for."""
    from v1.providers.sources.bevy_api import BevyApiEnumerator

    respx.get("https://events.example.com/api/event/").mock(
        return_value=httpx.Response(
            200,
            json={
                "count": 2,
                "results": [
                    {
                        "id": 1,
                        "url": "https://events.example.com/events/details/ours/",
                        "status": "Published",
                    },
                    {
                        "id": 2,
                        "url": "https://evil.example.net/events/details/theirs/",
                        "status": "Published",
                    },
                ],
            },
        )
    )
    source = SourceConfig.model_validate(
        {
            "id": "demo",
            "adapter": "bevy_api",
            "tier": SourceTier.T0_API,
            "base_url": "https://events.example.com",
            "external_ref": "42",
        }
    )
    enumerator = BevyApiEnumerator(source=source)
    items = await enumerator.enumerate_items()
    await enumerator.aclose()

    assert [item.external_id for item in items] == ["1"]


# ================================================= endpoint gate + fail-safe default


def test_environment_defaults_to_the_strict_setting() -> None:
    """Defaulting to `development` made every production safety check opt-in: deploy
    without setting the variable and the permissive mode applies silently."""
    from v1.config.settings import V1Settings

    assert V1Settings(_env_file=None).ENVIRONMENT == "production"
    assert V1Settings(_env_file=None).is_development is False

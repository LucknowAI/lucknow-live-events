"""Config provider name → search provider instance, plus the failover chain.

The only place that knows which adapter class implements which config `adapter:` value.
Everything else asks the chain, or asks for a provider by name.

Same construction rules as the LLM registry: eager and validated at startup so a bad
provider fails on boot rather than mid-run, but connections stay lazy so building the
registry costs nothing and needs no key for providers that will not be used.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from v1.config.loader import LoadedConfig
from v1.config.schema import (
    LedgerKind,
    SearchAdapterKind,
    SearchConfig,
    SearchProviderConfig,
)
from v1.config.settings import V1Settings, get_settings
from v1.contracts.errors import ConfigError
from v1.contracts.search import SearchCapabilities
from v1.models import tenant_uuid
from v1.platform.budget import (
    InMemorySearchSpendLedger,
    PostgresSearchSpendLedger,
    SearchBudgetGuard,
    SearchSpendLedger,
)
from v1.platform.logging import get_logger
from v1.platform.quota import SearchFailoverChain
from v1.providers.search.base import BaseSearchProvider
from v1.providers.search.cache import (
    InMemorySerpCache,
    NullSerpCache,
    PostgresSerpCache,
    SerpCache,
)
from v1.providers.search.dataforseo import DataForSeoProvider
from v1.providers.search.fixture import FixtureSearchProvider
from v1.providers.search.serper import SerperProvider
from v1.providers.search.tavily import TavilyProvider

logger = get_logger("v1.search.registry")

SEARCH_ADAPTERS: dict[SearchAdapterKind, type[BaseSearchProvider]] = {
    SearchAdapterKind.SERPER: SerperProvider,
    SearchAdapterKind.DATAFORSEO: DataForSeoProvider,
    SearchAdapterKind.TAVILY: TavilyProvider,
    SearchAdapterKind.FIXTURE: FixtureSearchProvider,
}


@dataclass(frozen=True, slots=True)
class SearchProviderInfo:
    """What `GET /discovery/providers` reports about one provider."""

    name: str
    adapter: str
    capabilities: SearchCapabilities
    available: bool
    unavailable_reason: str | None
    in_chain: bool
    chain_position: int | None
    usd_per_query: Decimal | None
    deep_query_multiplier: Decimal | None
    pricing_source: str | None
    pricing_as_of: str | None
    consecutive_failures: int
    disabled_until: str | None


def build_search_ledger(loaded: LoadedConfig, search: SearchConfig) -> SearchSpendLedger:
    if search.budget.ledger is LedgerKind.POSTGRES:
        return PostgresSearchSpendLedger(tenant_uuid(loaded.config.tenant.slug))
    return InMemorySearchSpendLedger()


def build_search_budget_guard(
    loaded: LoadedConfig, search: SearchConfig, ledger: SearchSpendLedger | None = None
) -> SearchBudgetGuard:
    return SearchBudgetGuard(
        ledger=ledger if ledger is not None else build_search_ledger(loaded, search),
        monthly_cap_usd=search.budget.monthly_usd_cap,
        on_exceeded=search.budget.on_exceeded,
        timezone=loaded.config.tenant.timezone,
    )


def build_serp_cache(loaded: LoadedConfig, search: SearchConfig) -> SerpCache:
    if search.cache.backend is LedgerKind.POSTGRES:
        return PostgresSerpCache(
            tenant_uuid(loaded.config.tenant.slug), ttl_hours=search.cache.ttl_hours
        )
    return InMemorySerpCache(ttl_hours=search.cache.ttl_hours, max_entries=search.cache.max_entries)


class SearchRegistry:
    """All configured search providers, one budget guard, one cache, one chain."""

    def __init__(
        self,
        *,
        loaded: LoadedConfig,
        budget: SearchBudgetGuard,
        cache: SerpCache,
        allow_live: bool = False,
    ) -> None:
        search = loaded.config.search
        if search is None:  # pragma: no cover - callers check first
            raise ConfigError("no `search` section is configured")
        self._loaded = loaded
        self._search = search
        self._budget = budget
        self._cache = cache
        self._allow_live = allow_live
        self._providers: dict[str, BaseSearchProvider] = {}
        self._chains: dict[str, SearchFailoverChain] = {}
        self._build()

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_config(
        cls,
        loaded: LoadedConfig,
        *,
        ledger: SearchSpendLedger | None = None,
        cache: SerpCache | None = None,
        settings: V1Settings | None = None,
    ) -> SearchRegistry | None:
        """Build the registry, or `None` when the instance configures no search at all.

        A focused-only deployment is a legitimate zero-spend configuration, not a broken
        one, so the absence of a `search` section is not an error here.
        """
        search = loaded.config.search
        if search is None:
            logger.info(
                "search_not_configured",
                detail="no `search` section; general discovery is unavailable and focused "
                "discovery runs on its own",
            )
            return None
        return cls(
            loaded=loaded,
            budget=build_search_budget_guard(loaded, search, ledger),
            cache=cache if cache is not None else build_serp_cache(loaded, search),
            allow_live=(settings or get_settings()).SEARCH_ALLOW_LIVE,
        )

    def _build(self) -> None:
        for name, provider in self._search.providers.items():
            self._providers[name] = self._make(name, provider)

        # Recording sources. This is the one wiring that lets a *costless* provider make a
        # real paid query, so a paid source is attached only when the process has
        # explicitly opted into live spend — otherwise `record_from` in a committed config
        # file could turn a fixture-only CI run into a bill.
        for name, provider in self._search.providers.items():
            if not provider.record_from:
                continue
            target = self._providers[name]
            assert isinstance(target, FixtureSearchProvider)
            source = self._providers[provider.record_from]
            if source.config.is_paid and not self._allow_live:
                logger.warning(
                    "serp_fixture_recording_disabled",
                    provider=name,
                    record_from=provider.record_from,
                    detail=(
                        "record_from targets a paid provider and V1_SEARCH_ALLOW_LIVE is "
                        "unset; a missing fixture will raise FixtureMissing instead of "
                        "spending"
                    ),
                )
                continue
            target.set_record_source(source)

        for entry in self._loaded.search_availability.values():
            if entry.in_use and not entry.available:
                logger.warning(
                    "search_provider_unavailable_but_in_chain",
                    provider=entry.name,
                    reason=entry.reason,
                )

        logger.info(
            "search_registry_ready",
            providers=sorted(self._providers),
            chain=self._search.chain,
            budget_cap_usd=str(self._search.budget.monthly_usd_cap),
            budget_on_exceeded=str(self._search.budget.on_exceeded),
            ledger=str(self._search.budget.ledger),
            cache_backend=str(self._search.cache.backend),
            allow_deep_pages=self._search.allow_deep_pages,
        )

    def _make(self, name: str, config: SearchProviderConfig) -> BaseSearchProvider:
        adapter_cls = SEARCH_ADAPTERS.get(config.adapter)
        if adapter_cls is None:  # pragma: no cover - SearchAdapterKind is closed
            raise ConfigError(f"no adapter implementation for {config.adapter!r}")
        return adapter_cls(
            name=name,
            config=config,
            api_key=self._loaded.search_key_for(name),
            budget=self._budget,
            cache=self._cache,
            allow_deep_pages=self._search.allow_deep_pages,
            allow_live=self._allow_live,
        )

    # ------------------------------------------------------------------------ lookup

    @property
    def budget(self) -> SearchBudgetGuard:
        return self._budget

    @property
    def cache(self) -> SerpCache:
        return self._cache

    @property
    def chain_names(self) -> list[str]:
        return list(self._search.chain)

    def get(self, provider_name: str) -> BaseSearchProvider:
        provider = self._providers.get(provider_name)
        if provider is None:
            raise ConfigError(
                f"unknown search provider {provider_name!r}; configured: {sorted(self._providers)}"
            )
        self._loaded.require_search_available(provider_name)
        return provider

    def build_chain(self, *, only: str | None = None) -> SearchFailoverChain:
        """The ordered chain — **cached**, so the circuit breaker outlives one request.

        A chain rebuilt per request has no memory. Every `/discovery/run` would start with
        a clean bill of health, re-attempt a provider whose key is known-bad, wait out its
        timeouts again, and re-discover the failure that a cooldown was supposed to have
        parked for half an hour. `GET /discovery/providers` would likewise always report
        zero failures, which makes the health fields decoration.

        So it is built once per (process, chain-selection) and reused. That is also what
        makes `cooldown_minutes` mean what it says.
        """
        cache_key = only or "__chain__"
        cached = self._chains.get(cache_key)
        if cached is not None:
            return cached

        chain = self._build_chain(only=only)
        self._chains[cache_key] = chain
        return chain

    def _build_chain(self, *, only: str | None = None) -> SearchFailoverChain:
        """Construct a chain, skipping providers whose secret could not be resolved.

        Skipping rather than failing is deliberate: a contributor with a Serper key and no
        DataForSEO account should get a working two-link chain, not a boot failure. The
        omission is logged at construction, and an empty chain is still an error.
        """
        names = [only] if only else self._search.chain
        usable: list[BaseSearchProvider] = []
        skipped: dict[str, str] = {}

        for name in names:
            entry = self._loaded.search_availability.get(name)
            if entry is not None and not entry.available:
                skipped[name] = entry.reason or "unavailable"
                continue
            usable.append(self._providers[name])

        if skipped:
            logger.warning(
                "search_chain_reduced", skipped=skipped, remaining=[p.name for p in usable]
            )
        if not usable:
            raise ConfigError(
                "no usable search provider: every provider in search.chain is missing its "
                f"API key ({skipped}). Set one, or run with V1_SEARCH_FORCE_PROVIDER "
                "pointing at a fixture provider"
            )

        return SearchFailoverChain(
            providers=usable,  # type: ignore[arg-type]
            consecutive_failures=self._search.failover.consecutive_failures,
            cooldown_minutes=self._search.failover.cooldown_minutes,
        )

    def uncached(self, provider_name: str) -> BaseSearchProvider:
        """A clone of one provider with caching disabled.

        `POST /discovery/search` exists to show what a provider returns *right now*;
        serving it from a six-hour-old cache would defeat the one job it has.
        """
        original = self.get(provider_name)
        return type(original)(
            name=original.name,
            config=original.config,
            api_key=self._loaded.search_key_for(provider_name),
            budget=self._budget,
            cache=NullSerpCache(),
            allow_deep_pages=self._search.allow_deep_pages,
            allow_live=self._allow_live,
        )

    def providers(self, chain: SearchFailoverChain | None = None) -> list[SearchProviderInfo]:
        """Provider info, including live breaker state.

        Defaults to the cached default chain rather than an empty dict, so the health
        fields reflect what the engine has actually experienced this process instead of
        always reading zero.
        """
        if chain is None:
            chain = self._chains.get("__chain__")
        health = chain.health if chain is not None else {}
        infos: list[SearchProviderInfo] = []
        for name, provider in sorted(self._providers.items()):
            entry = self._loaded.search_availability.get(name)
            pricing = provider.config.pricing
            state = health.get(name)
            infos.append(
                SearchProviderInfo(
                    name=name,
                    adapter=provider.adapter,
                    capabilities=provider.capabilities,
                    available=entry.available if entry else False,
                    unavailable_reason=entry.reason if entry else "not resolved",
                    in_chain=name in self._search.chain,
                    chain_position=(
                        self._search.chain.index(name) if name in self._search.chain else None
                    ),
                    usd_per_query=pricing.usd_per_query if pricing else None,
                    deep_query_multiplier=pricing.deep_query_multiplier if pricing else None,
                    pricing_source=pricing.source if pricing else None,
                    pricing_as_of=pricing.as_of.isoformat() if pricing else None,
                    consecutive_failures=state.consecutive_failures if state else 0,
                    disabled_until=(
                        state.disabled_until.isoformat() if state and state.disabled_until else None
                    ),
                )
            )
        return infos

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()

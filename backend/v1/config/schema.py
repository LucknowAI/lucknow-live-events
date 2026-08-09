"""Pydantic models for the V1 instance configuration file.

This module is the *contract* for `instance.yaml`. It is intentionally strict
(`extra="forbid"`) so a typo'd key is a startup failure rather than a silently ignored
setting — `13 §4` asks for fail-fast, loud config validation.

Two validations here are the reason this file is worth reading:

* **Paid profiles must declare pricing.** A budget circuit breaker that cannot price a
  call is decoration. An un-priced paid profile aborts startup.
* **Declared capabilities are checked against a known-provider matrix.** A profile may
  not claim `native_schema` on a provider/model combination that is documented not to
  enforce schemas — otherwise the port would trust hard enforcement it never had.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from v1.contracts.llm import StructuredOutputStrategy
from v1.contracts.search import DEFAULT_NUM, SearchCapabilities, SearchIndex
from v1.contracts.source import ENUMERABLE_TIERS, SourceTier

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"})


class AdapterKind(StrEnum):
    GEMINI_NATIVE = "gemini_native"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA_NATIVE = "ollama_native"
    FIXTURE = "fixture"
    MOCK = "mock"


COSTLESS_ADAPTERS = frozenset({AdapterKind.FIXTURE, AdapterKind.MOCK})


class OnBudgetExceeded(StrEnum):
    HALT = "halt"
    DEGRADE_TO_FIXTURE = "degrade_to_fixture"


class LedgerKind(StrEnum):
    MEMORY = "memory"
    POSTGRES = "postgres"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PricingConfig(_Strict):
    """Token prices with provenance.

    `source` and `as_of` are required on purpose: model prices and model availability
    both move (Gemini 2.0 is already shut down, 2.5 Flash is on a deprecation path), so
    a price with no date is a number nobody can audit.
    """

    input_usd_per_mtok: Decimal = Field(ge=0)
    output_usd_per_mtok: Decimal = Field(ge=0)
    source: str = Field(min_length=1)
    as_of: date


class LLMProfileConfig(_Strict):
    """One named, swappable way to call a model."""

    adapter: AdapterKind
    model: str | None = None
    base_url: str | None = None
    api_key_ref: str | None = None
    structured_output: StructuredOutputStrategy = StructuredOutputStrategy.NATIVE_SCHEMA
    supports_tools: bool = False

    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=4096, gt=0, le=131_072)
    timeout_s: float = Field(default=60.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=8)

    thinking_budget: int | None = Field(default=None, ge=0)
    """Gemini-only. Thinking tokens are billed as output, so this is a real cost knob.
    Left null by default: the model's own default applies, and we would rather see the
    thinking cost on the ledger than silently disable reasoning for every task."""

    pricing: PricingConfig | None = None
    extra_body: dict = Field(default_factory=dict)
    """Verbatim additions to the request body. Used for provider-specific knobs such as
    OpenRouter's `provider.require_parameters`, without teaching the adapter about
    OpenRouter."""

    fixture_dir: str | None = None
    """Only for the `fixture` adapter: directory of recorded responses."""

    record_from: str | None = None
    """Only for the `fixture` adapter: profile to record a missing fixture from, once.
    Leave null in CI so a missing fixture is a hard failure rather than a live call."""

    @property
    def host(self) -> str | None:
        if not self.base_url:
            return None
        return (urlparse(self.base_url).hostname or "").lower() or None

    @property
    def is_local(self) -> bool:
        """A localhost runtime, or an adapter that never touches the network."""
        if self.adapter in COSTLESS_ADAPTERS:
            return True
        host = self.host
        return host is not None and host in LOCAL_HOSTS

    @property
    def is_paid(self) -> bool:
        return self.adapter not in COSTLESS_ADAPTERS and not self.is_local

    @model_validator(mode="after")
    def _validate_profile(self) -> LLMProfileConfig:
        if self.adapter is AdapterKind.OLLAMA_NATIVE and not self.base_url:
            # Ollama's default listen address. Explicit here rather than in the adapter so
            # the effective value shows up in `GET /config`.
            self.base_url = "http://localhost:11434"

        if self.adapter is AdapterKind.OPENAI_COMPATIBLE and not self.base_url:
            raise ValueError("openai_compatible profiles require `base_url`")

        if self.adapter not in COSTLESS_ADAPTERS and not self.model:
            raise ValueError(f"adapter {self.adapter} requires `model`")

        if self.adapter is AdapterKind.GEMINI_NATIVE and not self.api_key_ref:
            raise ValueError("gemini_native profiles require `api_key_ref`")

        if self.api_key_ref is not None and not ENV_NAME_RE.match(self.api_key_ref):
            # Catches someone pasting the key itself into the *_ref field. Secrets belong
            # in the environment or a mounted file, never in a committed YAML.
            raise ValueError(
                "`api_key_ref` must be an UPPER_SNAKE environment variable name, not a "
                "literal secret value"
            )

        if self.is_paid and self.pricing is None:
            raise ValueError(
                f"profile uses paid adapter {self.adapter} and must declare `pricing` — "
                "the budget breaker cannot cap spend it cannot price"
            )

        if self.adapter in COSTLESS_ADAPTERS and self.pricing is not None:
            raise ValueError(f"adapter {self.adapter} costs nothing; remove `pricing`")

        if self.adapter is not AdapterKind.FIXTURE and (self.fixture_dir or self.record_from):
            raise ValueError("`fixture_dir` and `record_from` apply only to the `fixture` adapter")

        _check_declared_capability(self)
        return self


def _check_declared_capability(profile: LLMProfileConfig) -> None:
    """Reject a profile that claims stronger structured-output support than it has.

    Only *known* providers are checked. An unrecognised `base_url` is left alone — we
    have no documented matrix for it, and guessing would block legitimate endpoints.

    Sources: Groq restricts `strict: true` constrained decoding to `openai/gpt-oss-*`
    and ignores `strict` on every other model
    (https://console.groq.com/docs/structured-outputs); Ollama enforces schemas through
    llama.cpp grammars on the native `format` field
    (https://docs.ollama.com/capabilities/structured-outputs).
    """
    strategy = profile.structured_output
    adapter = profile.adapter

    if adapter in COSTLESS_ADAPTERS:
        return

    native = StructuredOutputStrategy.NATIVE_SCHEMA
    if adapter is AdapterKind.GEMINI_NATIVE and strategy is not native:
        raise ValueError(
            "gemini_native always uses `response_schema`; declare structured_output: native_schema"
        )

    if adapter is AdapterKind.OLLAMA_NATIVE and strategy in {
        StructuredOutputStrategy.JSON_SCHEMA_BEST_EFFORT,
        StructuredOutputStrategy.TOOL_CALL,
    }:
        raise ValueError(
            "ollama_native supports native_schema (grammar-constrained), json_object or "
            f"prompt_only — not {strategy}"
        )

    if adapter is AdapterKind.OPENAI_COMPATIBLE and profile.host == "api.groq.com":
        model = profile.model or ""
        strict_capable = model.startswith("openai/gpt-oss")
        if strategy is StructuredOutputStrategy.NATIVE_SCHEMA and not strict_capable:
            raise ValueError(
                f"Groq enforces schemas (`strict: true`) only on openai/gpt-oss-* models; "
                f"{model!r} would silently ignore it. Declare "
                "structured_output: json_schema_best_effort instead"
            )

    if strategy is StructuredOutputStrategy.TOOL_CALL and not profile.supports_tools:
        raise ValueError("structured_output: tool_call requires supports_tools: true")


class BudgetConfig(_Strict):
    daily_usd_cap: Decimal = Field(gt=0)
    on_exceeded: OnBudgetExceeded = OnBudgetExceeded.HALT
    ledger: LedgerKind = LedgerKind.MEMORY
    degrade_profile: str | None = None
    """Profile used when `on_exceeded: degrade_to_fixture` trips. Must be costless."""

    @model_validator(mode="after")
    def _validate_budget(self) -> BudgetConfig:
        if self.on_exceeded is OnBudgetExceeded.DEGRADE_TO_FIXTURE and not self.degrade_profile:
            raise ValueError("on_exceeded: degrade_to_fixture requires `degrade_profile`")
        return self


class LLMConfig(_Strict):
    default: str
    budget: BudgetConfig
    profiles: dict[str, LLMProfileConfig]
    tasks: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_references(self) -> LLMConfig:
        if not self.profiles:
            raise ValueError("`llm.profiles` must define at least one profile")

        if self.default not in self.profiles:
            raise ValueError(
                f"llm.default = {self.default!r} is not a configured profile "
                f"(have: {sorted(self.profiles)})"
            )

        for task, profile_name in self.tasks.items():
            if profile_name not in self.profiles:
                raise ValueError(
                    f"llm.tasks.{task} = {profile_name!r} is not a configured profile "
                    f"(have: {sorted(self.profiles)})"
                )

        for name, profile in self.profiles.items():
            source = profile.record_from
            if source is None:
                continue
            if source not in self.profiles:
                raise ValueError(
                    f"llm.profiles.{name}.record_from = {source!r} is not a configured profile"
                )
            if source == name:
                raise ValueError(f"llm.profiles.{name}.record_from cannot reference itself")
            if self.profiles[source].adapter in COSTLESS_ADAPTERS:
                raise ValueError(
                    f"llm.profiles.{name}.record_from = {source!r} must be a live provider; "
                    "recording from another costless adapter records nothing real"
                )

        degrade = self.budget.degrade_profile
        if degrade is not None:
            if degrade not in self.profiles:
                raise ValueError(
                    f"llm.budget.degrade_profile = {degrade!r} is not a configured profile"
                )
            if self.profiles[degrade].adapter not in COSTLESS_ADAPTERS:
                raise ValueError(
                    f"llm.budget.degrade_profile = {degrade!r} must use a costless adapter "
                    "(fixture or mock) — degrading to another paid provider is not a cap"
                )
        return self

    def profile_for_task(self, task: str) -> str:
        """Resolve a task name to a profile name, falling back to the default."""
        return self.tasks.get(task, self.default)

    @property
    def in_use_profiles(self) -> set[str]:
        """Profiles that will actually be called, so their secrets are resolved eagerly."""
        names = {self.default, *self.tasks.values()}
        if self.budget.degrade_profile:
            names.add(self.budget.degrade_profile)
        return names


class TenantConfig(_Strict):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _validate_timezone(self) -> TenantConfig:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {self.timezone!r}") from exc
        return self


class LocalityConfig(_Strict):
    """The tenant's vocabulary: what "here" and "our communities" mean.

    Every one of these is a value the engine would otherwise hardcode. They render into
    dork templates, they filter sitemap URLs, and in Phase 5 they drive the relevance
    score. Swapping this block for another city's must change discovery behaviour with no
    code change — that is the config-over-code proof (ADR-013, `13 §2`).
    """

    city_keywords: list[str] = Field(default_factory=list, max_length=100)
    community_names: list[str] = Field(default_factory=list, max_length=200)
    institution_names: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _validate_locality(self) -> LocalityConfig:
        for name, values in (
            ("city_keywords", self.city_keywords),
            ("community_names", self.community_names),
            ("institution_names", self.institution_names),
        ):
            if any(not value.strip() for value in values):
                raise ValueError(f"locality.{name} contains an empty entry")
        return self

    @property
    def all_terms(self) -> list[str]:
        return [*self.city_keywords, *self.community_names, *self.institution_names]


# =============================================================================== search


class SearchAdapterKind(StrEnum):
    """The search adapters that exist.

    Deliberately short. Every other option on the 2026 market is either retired or
    deprecated, and writing an adapter for one is dead code that still has to be reviewed,
    linted and kept compiling:

    * **Bing Search API** — retired 11 August 2025.
    * **Google Custom Search JSON API** — closed to new customers, fully retired 1 Jan 2027.
    * **Brave Search API** — free tier eliminated February 2026; at ~$5/1k it is 5–16×
      Serper on an independent index, and our dorks are written in Google operator syntax
      that index does not honour. Dropped from the chain *and* not written.
    * **Gemini / ADK grounded search** — its terms prohibit building a database out of
      grounded results (ADR-024), so it cannot sit behind this port at all.

    Adding one back is a new enum member plus an adapter class; nothing else changes.
    """

    SERPER = "serper"
    DATAFORSEO = "dataforseo"
    TAVILY = "tavily"
    FIXTURE = "fixture"


COSTLESS_SEARCH_ADAPTERS = frozenset({SearchAdapterKind.FIXTURE})

ADAPTER_CAPABILITIES: dict[SearchAdapterKind, dict[str, object]] = {
    # These are facts about the vendor, not choices an operator gets to make — so unlike
    # the LLM layer (where the model changes what the provider can do) they are derived
    # from the adapter rather than declared in the file. Overriding one *upward* is
    # rejected below; there is no way to make Tavily paginate by writing YAML.
    SearchAdapterKind.SERPER: {
        # https://serper.dev — Google index, full operator syntax, `page` is 1-based.
        "supports_pagination": True,
        "supports_operators": True,
        "max_num": 100,
        "index": SearchIndex.GOOGLE,
    },
    SearchAdapterKind.DATAFORSEO: {
        # https://docs.dataforseo.com/v3/serp-google-organic-overview/ — Google index.
        # Paginates by `depth` rather than a page number; the adapter converts.
        "supports_pagination": True,
        "supports_operators": True,
        "max_num": 100,
        "index": SearchIndex.GOOGLE,
    },
    SearchAdapterKind.TAVILY: {
        # https://docs.tavily.com/documentation/api-reference/endpoint/search — no page or
        # offset parameter exists, `max_results` caps at 20, and operator support is weak.
        "supports_pagination": False,
        "supports_operators": False,
        "max_num": 20,
        "index": SearchIndex.AGGREGATED,
    },
    SearchAdapterKind.FIXTURE: {
        "supports_pagination": True,
        "supports_operators": True,
        "max_num": 100,
        "index": SearchIndex.REPLAY,
    },
}


class DataForSeoMode(StrEnum):
    STANDARD = "standard"
    """Async: task_post → poll → task_get. ~$0.60/1k, seconds of latency."""

    LIVE = "live"
    """Synchronous single request. ~$2.00/1k, no polling."""


class OnSearchBudgetExceeded(StrEnum):
    HALT = "halt"
    DEGRADE_TO_FOCUSED_ONLY = "degrade_to_focused_only"
    """Keep running, but skip general discovery entirely. Focused ingestion is free, so
    the engine keeps finding events from known communities with zero spend."""


class SearchPricingConfig(_Strict):
    """Query prices with provenance. Same rule as LLM pricing: no price, no paid provider.

    SERP vendors bill in credits, not dollars, and the credit→dollar rate depends on which
    prepaid pack was bought. So this is an *accounting* price, dated and sourced, not a
    quote — and `GET /discovery/providers` shows it next to the spend so a stale number is
    visible rather than silently wrong.
    """

    usd_per_query: Decimal = Field(ge=0)
    deep_query_multiplier: Decimal = Field(default=Decimal(1), ge=1)
    """Multiplier when more than 10 results are requested. Serper charges 2 credits for
    11–100 results, which is the single easiest way to accidentally double the bill."""

    source: str = Field(min_length=1)
    as_of: date


class SearchProviderConfig(_Strict):
    """One named, swappable way to run a web search."""

    adapter: SearchAdapterKind
    base_url: str | None = None
    api_key_ref: str | None = None
    pricing: SearchPricingConfig | None = None

    timeout_s: float = Field(default=20.0, gt=0, le=300)
    max_retries: int = Field(default=2, ge=0, le=8)

    mode: DataForSeoMode | None = None
    """DataForSEO only: `standard` (async, cheap) or `live` (sync, ~3× the price)."""

    poll_interval_s: float = Field(default=2.0, gt=0, le=60)
    poll_timeout_s: float = Field(default=120.0, gt=0, le=600)
    """DataForSEO `standard` mode only: how long to wait for a posted task."""

    max_num: int | None = Field(default=None, gt=0, le=100)
    """Optional *downward* override of the adapter's documented result ceiling. Raising it
    above what the vendor supports is rejected — see `_check_search_capability`."""

    fixture_dir: str | None = None
    record_from: str | None = None
    """Fixture adapter only: provider to record a missing response from, once. Null in CI
    so a missing fixture is a hard failure rather than a live query."""

    extra_params: dict = Field(default_factory=dict)
    """Verbatim additions to the request body — vendor knobs the adapter need not know
    about. Redacted in `GET /config`, because a config author could put a token here."""

    @property
    def is_costless(self) -> bool:
        return self.adapter in COSTLESS_SEARCH_ADAPTERS

    @property
    def is_paid(self) -> bool:
        return not self.is_costless

    @property
    def capabilities(self) -> SearchCapabilities:
        facts = dict(ADAPTER_CAPABILITIES[self.adapter])
        if self.max_num is not None:
            facts["max_num"] = min(int(facts["max_num"]), self.max_num)  # type: ignore[arg-type]
        return SearchCapabilities(is_costless=self.is_costless, **facts)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def _validate_provider(self) -> SearchProviderConfig:
        if self.is_paid and self.api_key_ref is None:
            raise ValueError(f"search adapter {self.adapter} requires `api_key_ref`")

        if self.api_key_ref is not None and not ENV_NAME_RE.match(self.api_key_ref):
            raise ValueError(
                "`api_key_ref` must be an UPPER_SNAKE environment variable name, not a "
                "literal secret value"
            )

        if self.is_paid and self.pricing is None:
            raise ValueError(
                f"search provider uses paid adapter {self.adapter} and must declare "
                "`pricing` — the budget breaker cannot cap spend it cannot price"
            )

        if self.is_costless and self.pricing is not None:
            raise ValueError(f"search adapter {self.adapter} costs nothing; remove `pricing`")

        if self.adapter is not SearchAdapterKind.FIXTURE and (self.fixture_dir or self.record_from):
            raise ValueError("`fixture_dir` and `record_from` apply only to the `fixture` adapter")

        if self.adapter is SearchAdapterKind.DATAFORSEO and self.mode is None:
            # Explicit rather than defaulted in the adapter: the two modes differ by ~3× in
            # price, and a price difference that lives in code is one nobody reviews.
            raise ValueError("dataforseo providers must declare `mode`: standard | live")

        if self.adapter is not SearchAdapterKind.DATAFORSEO and self.mode is not None:
            raise ValueError("`mode` applies only to the `dataforseo` adapter")

        _check_search_capability(self)
        return self


def _check_search_capability(provider: SearchProviderConfig) -> None:
    """Reject a `max_num` above what the vendor documents.

    The failure this prevents is quiet: Tavily caps `max_results` at 20, so a config
    asking for 50 gets 20 back, the walker counts a short page as "few novel URLs", and
    the novelty floor declares a perfectly productive query exhausted.
    """
    if provider.max_num is None:
        return
    ceiling = int(ADAPTER_CAPABILITIES[provider.adapter]["max_num"])  # type: ignore[arg-type]
    if provider.max_num > ceiling:
        raise ValueError(
            f"search adapter {provider.adapter} returns at most {ceiling} results per "
            f"query; max_num: {provider.max_num} would be silently truncated"
        )


class SearchBudgetConfig(_Strict):
    """SERP spend cap. Monthly, unlike the LLM cap, because SERP volume is a monthly
    quantity by design (~1,400 queries/month) and a daily cap at that rate would be noise.
    Same accounting module, different window."""

    monthly_usd_cap: Decimal = Field(gt=0)
    on_exceeded: OnSearchBudgetExceeded = OnSearchBudgetExceeded.HALT
    ledger: LedgerKind = LedgerKind.MEMORY


class SearchCacheConfig(_Strict):
    """Response cache keyed `sha256(query|provider|page|num|gl|hl|tbs)`."""

    ttl_hours: float = Field(default=6.0, gt=0, le=720)
    backend: LedgerKind = LedgerKind.MEMORY
    """`postgres` is the only setting that does anything in production. A discovery run is
    a short-lived cron process, so an in-process cache in that shape has a 0% hit rate —
    it would be theatre. `v1.api.app.validate_runtime` refuses to start a non-development
    environment on the memory backend for exactly that reason."""

    max_entries: int = Field(default=5000, gt=0)
    """Memory backend only: bound the process's own footprint."""


class SearchFailoverConfig(_Strict):
    consecutive_failures: int = Field(default=3, ge=1, le=20)
    """Failures before a provider is taken out of the chain for `cooldown_minutes`."""

    cooldown_minutes: float = Field(default=30.0, gt=0, le=1440)


class SearchDefaults(_Strict):
    num: int = Field(default=DEFAULT_NUM, ge=1, le=100)
    gl: str = Field(default="in", min_length=2, max_length=8)
    hl: str = Field(default="en", min_length=2, max_length=8)


class SearchConfig(_Strict):
    chain: list[str] = Field(min_length=1)
    """Ordered failover. Names must be keys of `providers`."""

    budget: SearchBudgetConfig
    providers: dict[str, SearchProviderConfig]
    cache: SearchCacheConfig = Field(default_factory=SearchCacheConfig)
    defaults: SearchDefaults = Field(default_factory=SearchDefaults)
    failover: SearchFailoverConfig = Field(default_factory=SearchFailoverConfig)

    allow_deep_pages: bool = False
    """Permit `num > 10`. Off by default: Serper charges 2 credits for 11–100 results, so
    two pages of 10 cost less than one page of 100 and return the same top 20."""

    @model_validator(mode="after")
    def _validate_search(self) -> SearchConfig:
        if not self.providers:
            raise ValueError("`search.providers` must define at least one provider")

        for name in self.chain:
            if name not in self.providers:
                raise ValueError(
                    f"search.chain entry {name!r} is not a configured provider "
                    f"(have: {sorted(self.providers)})"
                )
        if len(set(self.chain)) != len(self.chain):
            raise ValueError("search.chain contains a duplicate provider")

        for name, provider in self.providers.items():
            source = provider.record_from
            if source is None:
                continue
            if source not in self.providers:
                raise ValueError(
                    f"search.providers.{name}.record_from = {source!r} is not a configured provider"
                )
            if source == name:
                raise ValueError(f"search.providers.{name}.record_from cannot reference itself")
            if self.providers[source].is_costless:
                raise ValueError(
                    f"search.providers.{name}.record_from = {source!r} must be a live "
                    "provider; recording from another costless adapter records nothing real"
                )

        if not self.allow_deep_pages and self.defaults.num > DEFAULT_NUM:
            raise ValueError(
                f"search.defaults.num = {self.defaults.num} exceeds {DEFAULT_NUM} while "
                "search.allow_deep_pages is false. Serper bills 11–100 results as two "
                "credits; set allow_deep_pages: true if you mean it"
            )

        for name, provider in self.providers.items():
            ceiling = provider.capabilities.max_num
            if self.defaults.num > ceiling:
                raise ValueError(
                    f"search.defaults.num = {self.defaults.num} exceeds the {ceiling}-result "
                    f"ceiling of provider {name!r} ({provider.adapter}); it would be "
                    "silently truncated"
                )
        return self

    @property
    def paid_chain_members(self) -> list[str]:
        return [name for name in self.chain if self.providers[name].is_paid]


# ============================================================================ discovery


class TemplateTier(StrEnum):
    A = "A"
    """Known-good platforms. Runs every general-discovery run."""

    B = "B"
    """Daily. Broader, noisier."""

    C = "C"
    """Weekly, exploratory — the net that finds platforms nobody told us about."""


class UrlRulesConfig(_Strict):
    """The URL classifier's rule table, as data.

    V0's equivalent is one large regex in code, which is why "why was this URL dropped?"
    is currently unanswerable. Here each rule has an id that travels with the verdict.
    """

    blocked_hosts: list[str] = Field(default_factory=list, max_length=200)
    """Hosts that are never event pages — social networks, video sites, shorteners.
    Matched on the registrable host and any subdomain."""

    event_patterns: list[str] = Field(default_factory=list, max_length=200)
    """Regexes over the normalized URL that mark a single event page. `inurl:/events/
    details/` is the strongest signal we have, so it is a rule, not a guess."""

    listing_patterns: list[str] = Field(default_factory=list, max_length=200)
    """Regexes that mark a directory/search/browse page."""

    allowed_schemes: list[str] = Field(default_factory=lambda: ["http", "https"], max_length=8)

    @model_validator(mode="after")
    def _validate_rules(self) -> UrlRulesConfig:
        for field_name, patterns in (
            ("event_patterns", self.event_patterns),
            ("listing_patterns", self.listing_patterns),
        ):
            for pattern in patterns:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(
                        f"discovery.url_rules.{field_name}: {pattern!r} is not a valid "
                        f"regex ({exc})"
                    ) from exc
        for scheme in self.allowed_schemes:
            if scheme not in {"http", "https"}:
                raise ValueError(
                    f"discovery.url_rules.allowed_schemes: {scheme!r} is not permitted; "
                    "only http and https are ever fetchable"
                )
        return self


class TriageConfig(_Strict):
    """Batched LLM triage for URLs no rule matched."""

    enabled: bool = True
    task: str = "url_triage"
    """Routed through `llm.tasks`, so which model triages URLs is one config line."""

    batch_size: int = Field(default=20, ge=1, le=100)
    max_batches: int = Field(default=3, ge=0, le=50)
    """Hard ceiling per run. Without it, one noisy exploratory template could turn a
    penny of search into dollars of LLM calls."""


class SourceConfig(_Strict):
    """One known source that can be enumerated without spending a search query."""

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    adapter: str = Field(min_length=1, max_length=32)
    """`bevy_api` | `sitemap`. Free-form so a new enumerator is a registry entry, not an
    enum edit in three files; unknown values fail at registry construction with the list
    of known adapters."""

    tier: SourceTier
    enabled: bool = True
    name: str | None = Field(default=None, max_length=200)

    base_url: str | None = Field(default=None, max_length=500)
    url: str | None = Field(default=None, max_length=2000)
    external_ref: str | None = Field(default=None, max_length=128)
    """The source's own identifier — a Bevy chapter id, a feed's calendar id."""

    include_patterns: list[str] = Field(default_factory=list, max_length=100)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=100)
    """Regexes over the URL, applied *before* any page is fetched. Commudle's events
    sitemap has ~5,000 URLs with the community slug in the path, so the right patterns
    reduce it to the relevant ones for zero requests."""

    child_include_patterns: list[str] = Field(default_factory=list, max_length=50)
    """Sitemap-index only: which child sitemaps are worth *requesting*. Empty means all.

    Measured, not guessed. Commudle's index lists blogs, builds, hackathons, jobs, labs,
    case studies, newsletters, users, communities and events; following all of them pulled
    **205,336 URLs in 117 seconds** to find the 178 we wanted. `sitemap_events\\.xml` makes
    that one request. An exclude list would have to name every uninteresting child and
    would silently start crawling the next one somebody adds — an allowlist cannot."""

    unfiltered_sentinel: int | None = Field(default=None, gt=0)
    """Result count at or above which the response is treated as unfiltered and the
    enumeration fails. Bevy silently ignores unknown query params and answers a typo'd
    filter with the entire 70,000-event global firehose under HTTP 200."""

    max_items: int = Field(default=500, gt=0, le=50_000)
    timeout_s: float = Field(default=30.0, gt=0, le=300)

    @model_validator(mode="after")
    def _validate_source(self) -> SourceConfig:
        if self.tier not in ENUMERABLE_TIERS:
            raise ValueError(
                f"source {self.id!r} declares tier {self.tier}; discovery can only "
                f"enumerate {sorted(str(t) for t in ENUMERABLE_TIERS)}. T2/T3 sources are "
                "discovered by the general strategy and fetched in a later phase"
            )
        for field_name, patterns in (
            ("include_patterns", self.include_patterns),
            ("exclude_patterns", self.exclude_patterns),
            ("child_include_patterns", self.child_include_patterns),
        ):
            for pattern in patterns:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(
                        f"source {self.id!r} {field_name}: {pattern!r} is not a valid regex ({exc})"
                    ) from exc
        return self


class DiscoveryConfig(_Strict):
    novelty_floor: float = Field(default=0.15, ge=0.0, le=1.0)
    """Stop descending pages when the share of never-seen URLs falls below this. An
    economic stop condition, not a quality one: below it, the page is not paying for
    itself."""

    exhausted_reset_hours: float = Field(default=168.0, gt=0, le=8760)
    max_pages_default: int = Field(default=3, ge=1, le=20)
    templates_path: str = "templates.yaml"
    """Resolved relative to the instance config file."""

    url_rules: UrlRulesConfig = Field(default_factory=UrlRulesConfig)
    triage: TriageConfig = Field(default_factory=TriageConfig)
    sources: list[SourceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_discovery(self) -> DiscoveryConfig:
        seen: set[str] = set()
        for source in self.sources:
            if source.id in seen:
                raise ValueError(f"duplicate discovery source id {source.id!r}")
            seen.add(source.id)
        return self

    def source_by_id(self, source_id: str) -> SourceConfig | None:
        return next((source for source in self.sources if source.id == source_id), None)


class TemplateConfig(_Strict):
    """One dork template. Data, so tuning discovery is a config change, not a deploy."""

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    tier: TemplateTier
    query: str = Field(min_length=1, max_length=2000)
    max_pages: int = Field(default=3, ge=1, le=20)
    enabled: bool = True
    requires_operators: bool = True
    """Nearly every template is Google operator syntax. A provider without operator
    support skips these with a logged reason instead of returning results that quietly
    ignored half the query."""

    num: int | None = Field(default=None, ge=1, le=100)
    tbs: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)


class TemplatesFile(_Strict):
    """`templates.yaml` — the dork template bank."""

    templates: list[TemplateConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_templates(self) -> TemplatesFile:
        seen: set[str] = set()
        for template in self.templates:
            if template.id in seen:
                raise ValueError(f"duplicate template id {template.id!r}")
            seen.add(template.id)
        return self

    def by_tier(self, tiers: set[TemplateTier]) -> list[TemplateConfig]:
        return [t for t in self.templates if t.enabled and t.tier in tiers]


class EngineConfig(_Strict):
    """The whole config file. Sections are added by the phase that consumes them."""

    config_version: int = 1
    tenant: TenantConfig
    llm: LLMConfig
    locality: LocalityConfig = Field(default_factory=LocalityConfig)
    search: SearchConfig | None = None
    """Optional: an instance that only runs focused ingestion needs no SERP provider at
    all, and that is a legitimate zero-spend deployment rather than a broken one."""

    discovery: DiscoveryConfig | None = None

    @model_validator(mode="after")
    def _validate_cross_section(self) -> EngineConfig:
        if self.discovery is not None and self.discovery.triage.enabled:
            task = self.discovery.triage.task
            # `profile_for_task` falls back to `llm.default`, so this cannot dangle — but a
            # task named here and absent from `llm.tasks` means URL triage silently runs on
            # the (possibly expensive) default model. Surfacing it beats discovering it on
            # the bill.
            if task not in self.llm.tasks:
                raise ValueError(
                    f"discovery.triage.task = {task!r} has no entry in llm.tasks, so URL "
                    f"triage would run on llm.default ({self.llm.default!r}). Add it to "
                    "llm.tasks, or set discovery.triage.enabled: false"
                )
        return self

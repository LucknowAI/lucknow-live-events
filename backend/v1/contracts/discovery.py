"""Discovery contracts — what both strategies produce, and what a run reports.

Discovery's whole job is to hand the rest of the engine a list of candidates. `focused`
(platform APIs and feeds) and `general` (SERP + dork templates) look nothing alike
internally and converge on exactly one output type, which is ADR-003: one source-agnostic
lifecycle. Adding a third strategy later — email ingestion, organizer submission — is a
new `DiscoveryStrategy`, not a change to anything downstream.

`DiscoveredItem.structured` is the field worth defending. A T0 API hands back a complete,
already-structured event record; throwing it away and re-fetching the URL would convert a
free, confidence-1.0 result into a paid extraction. Discovery does not interpret the
payload, it only refuses to discard it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from v1.contracts.source import SourceTier
from v1.contracts.verdicts import UrlClassification, UrlVerdict


class StrategyKind(StrEnum):
    FOCUSED = "focused"
    """Known platforms, enumerated through an API or a feed. Zero search spend."""

    GENERAL = "general"
    """Dork templates through a SERP provider. Paid per query, finds the long tail."""


class DiscoveredItem(BaseModel):
    """One candidate produced by either strategy."""

    model_config = ConfigDict(frozen=True)

    url: str = Field(min_length=1, max_length=2048)
    """Normalised, canonical form. This is what `url_hash` and dedup are computed from."""

    raw_url: str = Field(min_length=1, max_length=2048)
    """Exactly as the provider gave it, so a normalizer bug is diagnosable after the fact."""

    strategy: StrategyKind
    origin: str = Field(min_length=1, max_length=64)
    """Template id (general) or source id (focused). Answers "what did we pay for this"."""

    tier: SourceTier
    verdict: UrlVerdict = UrlVerdict.UNKNOWN
    verdict_reason: str = Field(default="not classified", max_length=500)

    title: str | None = Field(default=None, max_length=1000)
    snippet: str | None = Field(default=None, max_length=4000)
    external_id: str | None = Field(default=None, max_length=128)
    """The source's own identifier, when it has one. `(source, external_id)` is the
    idempotency key a T0 source can be upserted on without guessing from the URL."""

    structured: dict[str, Any] | None = None
    """Verbatim T0 payload. Never interpreted here — see the module docstring."""

    first_seen: bool = True
    """False when `discovered_url` already had this hash. Drives the novelty floor."""

    seen_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def is_candidate(self) -> bool:
        """Whether this should proceed to fetch. Listings and irrelevancies are recorded,
        not followed."""
        return self.verdict in {UrlVerdict.EVENT_PAGE, UrlVerdict.UNKNOWN}


class TemplateOutcome(BaseModel):
    """Per-template accounting for one general-discovery run."""

    model_config = ConfigDict(frozen=True)

    template_id: str
    rendered_query: str
    provider: str
    pages_fetched: int = 0
    results_seen: int = 0
    novel_urls: int = 0
    novelty_ratio: float = 0.0
    stopped_because: str = ""
    """`novelty_floor` | `max_pages` | `no_results` | `exhausted` | `unsupported` |
    `provider_error` | `budget` — the reason the walk stopped, in the report rather than
    only in a log line."""

    cost_usd: Decimal = Decimal(0)
    cache_hits: int = 0


class SourceOutcome(BaseModel):
    """Per-source accounting for one focused-discovery run."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    adapter: str
    tier: SourceTier
    items_enumerated: int = 0
    novel_urls: int = 0
    error: str | None = None


class DiscoveryReport(BaseModel):
    """What one discovery run did, cost, and decided. Returned by preview and run alike."""

    model_config = ConfigDict(frozen=True)

    strategy: StrategyKind | None = None
    started_at: datetime
    duration_ms: int
    dry_run: bool

    items: list[DiscoveredItem] = Field(default_factory=list)
    classifications: list[UrlClassification] = Field(default_factory=list)
    templates: list[TemplateOutcome] = Field(default_factory=list)
    sources: list[SourceOutcome] = Field(default_factory=list)

    queries_issued: int = 0
    cache_hits: int = 0
    search_cost_usd: Decimal = Decimal(0)
    llm_cost_usd: Decimal = Decimal(0)
    triage_calls: int = 0
    provider_switches: list[str] = Field(default_factory=list)
    """Human-readable failover events, e.g. `serper→dataforseo: provider_rate_limited`."""

    errors: list[str] = Field(default_factory=list)

    @property
    def total_cost_usd(self) -> Decimal:
        return self.search_cost_usd + self.llm_cost_usd

    def counts_by_verdict(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[str(item.verdict)] = counts.get(str(item.verdict), 0) + 1
        return counts


# --------------------------------------------------------------- LLM triage schema


class TriagedUrl(BaseModel):
    """One URL's verdict from the batched triage call.

    `index` rather than the URL itself: asking a model to echo back a 2 KB URL wastes
    output tokens and invites a subtly mangled copy that no longer matches anything. The
    caller holds the list; the model only has to point at it.
    """

    index: int = Field(ge=0, description="Zero-based position in the list that was sent.")
    verdict: str = Field(
        description="One of: event_page, listing, irrelevant.",
        max_length=32,
    )
    reason: str = Field(max_length=200, description="Short justification, for the log.")


class UrlTriageBatch(BaseModel):
    """Response schema for one batched URL-triage LLM call.

    Deliberately not `extra="forbid"` — see `v1.contracts.event` for why no schema
    reachable through the LLM port may set it.
    """

    verdicts: list[TriagedUrl] = Field(
        default_factory=list,
        max_length=100,
        description="One entry per URL in the list that was provided.",
    )

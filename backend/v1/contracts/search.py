"""Shared vocabulary for the web-search port.

Every module exchanges these types with the search layer. No vendor names, no SDK
imports — a module that speaks these types cannot tell whether Serper, DataForSEO,
Tavily or a recorded fixture answered.

The load-bearing type here is `SearchCapabilities`. Search providers are *not*
interchangeable in the way the parent plan assumed: Tavily has no pagination parameter at
all and weak operator support, while our entire dork strategy is written in Google
operator syntax. Pretending otherwise does not produce an error — it produces a run that
pays for page 1 three times and concludes the query is exhausted. So capability is
declared per provider and the pagination walker reads it before asking for a page.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_NUM = 10
"""Results per page. Ten, not a hundred, on purpose: Serper charges one credit for a
query returning up to 10 results and **two** credits for 11–100, so three pages at
`num=10` cost 3 credits where one `num=100` call costs 2 credits for 10× the noise. The
funnel refuses `num > 10` unless `search.allow_deep_pages` is explicitly set."""


class SearchIndex(StrEnum):
    """Which corpus a provider is actually searching. Affects whether dorks work.

    Only the corpora we actually reach are listed. Independent indexes (Brave) are not
    here because no adapter produces one — our templates are Google operator syntax, and
    an index that ignores `inurl:` returns plausible-looking results for a query it never
    really ran.
    """

    GOOGLE = "google"
    AGGREGATED = "aggregated"
    REPLAY = "replay"


class SearchOutcome(StrEnum):
    """Terminal outcome of one query, recorded on the SERP ledger."""

    OK = "ok"
    CACHE_HIT = "cache_hit"
    PROVIDER_ERROR = "provider_error"
    BUDGET_BLOCKED = "budget_blocked"
    SPEND_NOT_PERMITTED = "spend_not_permitted"
    """The process never opted in to live spend, so the query was not issued."""
    UNSUPPORTED = "unsupported"
    """The provider cannot serve this query shape (e.g. page 2 with no pagination)."""


class SearchCapabilities(BaseModel):
    """What a search provider can actually do, declared in config and validated at boot."""

    model_config = ConfigDict(frozen=True)

    supports_pagination: bool = True
    """False for providers with no page/offset parameter. The walker stops after page 1
    rather than paying to receive the same page again."""

    supports_operators: bool = True
    """Google operator syntax (`site:`, `inurl:`, `after:`, `OR`, `-`). A template that
    declares `requires_operators` is skipped on a provider without it, with a logged
    reason, instead of returning results that quietly ignore half the query."""

    max_num: int = Field(default=100, gt=0)
    index: SearchIndex = SearchIndex.GOOGLE
    is_costless: bool = False
    """True for fixture replay. Only a costless provider may be reached after the spend
    cap trips — failing over from a capped provider to another paid one is spending
    around the cap, not under it."""


class SearchQuery(BaseModel):
    """One page of one query, in provider-neutral form."""

    model_config = ConfigDict(frozen=True)

    q: str = Field(min_length=1, max_length=2048)
    page: int = Field(default=1, ge=1, le=100)
    num: int = Field(default=DEFAULT_NUM, ge=1, le=100)
    gl: str = Field(default="in", min_length=2, max_length=8)
    """Country bias, ISO-3166-1 alpha-2 lowercase. From config, never hardcoded."""

    hl: str = Field(default="en", min_length=2, max_length=8)
    tbs: str | None = Field(default=None, max_length=200)
    """Google time-based search parameter, when a template wants one."""

    template_id: str | None = Field(default=None, max_length=64)
    """Which template produced this. Carried through so a SERP result can be traced back
    to the query that cost money for it."""

    requires_operators: bool = False

    def cache_key(self, provider: str) -> str:
        """`sha256(query|provider|page|num|gl|hl|tbs)` — the SERP cache key.

        `template_id` is deliberately excluded: two templates that render to the same
        query string should share one cached response rather than pay twice.
        """
        payload = json.dumps(
            {
                "provider": provider,
                "q": self.q,
                "page": self.page,
                "num": self.num,
                "gl": self.gl,
                "hl": self.hl,
                "tbs": self.tbs,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def query_hash(self) -> str:
        """Identity of the *query text* alone — the pagination cursor's key."""
        return hashlib.sha256(self.q.encode("utf-8")).hexdigest()


class SerpResult(BaseModel):
    """One organic result. Normalised from whatever shape the provider returned."""

    model_config = ConfigDict(frozen=True)

    url: str = Field(min_length=1, max_length=2048)
    title: str = Field(default="", max_length=1000)
    snippet: str | None = Field(default=None, max_length=4000)
    position: int = Field(ge=1)
    page: int = Field(ge=1)
    published_at: str | None = Field(default=None, max_length=100)
    """Provider-reported date string, kept verbatim. Parsing it is Phase 5's job — a
    normalizer here would be a second place that decides what a date means."""

    template_id: str | None = Field(default=None, max_length=64)


class SearchResponse(BaseModel):
    """The result of one query through one provider, with its price attached."""

    model_config = ConfigDict(frozen=True)

    provider: str
    adapter: str
    query: SearchQuery
    results: list[SerpResult] = Field(default_factory=list)
    outcome: SearchOutcome = SearchOutcome.OK
    credits: int | None = None
    """Provider-reported credit consumption when it reports any (Serper, Tavily)."""

    cost_usd: Decimal = Decimal(0)
    cost_is_estimated: bool = True
    """True when the cost came from our config price rather than a provider-reported
    figure. Almost always true: SERP providers bill in credits, not dollars, per call."""

    latency_ms: int = 0
    cached: bool = False

    @property
    def result_count(self) -> int:
        return len(self.results)

"""Tavily — the tertiary provider, and the only recurring free tier left.

1,000 credits per month, renewing. That makes it the safety net when both paid providers
are down or capped, not a peer of them:

    POST https://api.tavily.com/search
    Authorization: Bearer tvly-<key>
    {"query": "...", "search_depth": "basic", "max_results": 10, "country": "india"}

    → {"query", "results": [{"title", "url", "content", "score", ...}],
       "response_time", "request_id", "usage": {"credits": 1}}

**Two limitations that are declared capabilities, not surprises.**

1. **No pagination.** There is no `page` or `offset` parameter in the API at all. Asking
   for "page 2" would return page 1, be billed as a query, and read to the novelty floor as
   "no new URLs" — so the walker never asks. `SearchCapabilities.supports_pagination` is
   False and the funnel raises `SearchUnsupported` before spending anything.
2. **Weak operator support.** Our templates are Google operator syntax (`site:`,
   `inurl:`, `after:`). Tavily searches an aggregated corpus and does not honour them, so a
   template declaring `requires_operators` is skipped here with a logged reason rather than
   returning confident-looking results for a query that was never really run.

`max_results` caps at 20. `basic`/`fast`/`ultra-fast` cost 1 credit; `advanced` costs 2 —
so the default stays `basic`, and the response's own `usage.credits` is recorded.
(https://docs.tavily.com/documentation/api-reference/endpoint/search)
"""

from __future__ import annotations

from typing import Any

from v1.contracts.errors import ProviderError
from v1.contracts.search import SearchQuery, SerpResult
from v1.providers.search.base import BaseSearchProvider, RawSearch

DEFAULT_BASE_URL = "https://api.tavily.com"
SEARCH_PATH = "/search"
MAX_RESULTS = 20


class TavilyProvider(BaseSearchProvider):
    adapter = "tavily"

    @property
    def _endpoint(self) -> str:
        base = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        return f"{base}{SEARCH_PATH}"

    async def _search(self, query: SearchQuery) -> RawSearch:
        body: dict[str, Any] = {
            "query": query.q,
            "search_depth": "basic",
            "max_results": min(query.num, MAX_RESULTS),
            "topic": "general",
            # Raw content doubles the payload and we do our own extraction in Phase 4;
            # answers are a generated summary we would have to treat as untrusted anyway.
            "include_answer": False,
            "include_raw_content": False,
            **self.config.extra_params,
        }

        payload = await self.post_json(
            self._endpoint,
            json_body=body,
            headers={
                "Authorization": f"Bearer {self._api_key or ''}",
                "Content-Type": "application/json",
            },
        )
        if not isinstance(payload, dict):
            raise ProviderError(
                f"{self.name} returned {type(payload).__name__} at the top level",
                provider=self.name,
            )

        rows = payload.get("results")
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise ProviderError(f"{self.name} returned a non-list `results`", provider=self.name)

        results: list[SerpResult] = []
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            url = row.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            results.append(
                SerpResult(
                    url=url.strip(),
                    title=str(row.get("title") or "")[:1000],
                    # Tavily returns extracted page content rather than a SERP snippet, so
                    # it is longer and differently shaped. Truncated to the same field so
                    # downstream code never has to know which provider answered.
                    snippet=(str(row["content"])[:4000] if row.get("content") else None),
                    position=index,
                    page=query.page,
                    template_id=query.template_id,
                )
            )

        usage = payload.get("usage")
        credits = usage.get("credits") if isinstance(usage, dict) else None
        return RawSearch(
            results=results,
            credits=int(credits) if isinstance(credits, int) else None,
            raw=payload,
        )

"""Serper.dev — the primary provider.

Real Google index, full operator syntax (the entire dork strategy depends on it), and the
cheapest credible per-query price. 2,500 free credits once per account, which covers the
whole build phase.

    POST https://google.serper.dev/search
    X-API-KEY: <key>
    {"q": "...", "gl": "in", "hl": "en", "num": 10, "page": 1}

    → {"searchParameters": {...},
       "organic": [{"title", "link", "snippet", "position", "date"?}, ...],
       "credits": 1}

**Credits.** One credit covers a query returning up to 10 results; 11–100 results costs
**two**. That is why `num` defaults to 10 and the funnel refuses more without an explicit
opt-in: `page=2,3` at one credit each is strictly cheaper than one 100-result call.
Purchased credits expire six months after purchase.
(https://apiserpent.com/blog/serper-pricing-credits-explained)

**403, not 401.** Verified 2026-08-08: both a missing key and an invalid key return
`403 {"message": "Unauthorized.", "statusCode": 403}`. The shared mapper treats 403 as a
hard auth failure for exactly this reason.
"""

from __future__ import annotations

from typing import Any

from v1.contracts.errors import ProviderError
from v1.contracts.search import SearchQuery, SerpResult
from v1.providers.search.base import BaseSearchProvider, RawSearch

DEFAULT_BASE_URL = "https://google.serper.dev"
SEARCH_PATH = "/search"


class SerperProvider(BaseSearchProvider):
    adapter = "serper"

    @property
    def _endpoint(self) -> str:
        base = (self.config.base_url or DEFAULT_BASE_URL).rstrip("/")
        return f"{base}{SEARCH_PATH}"

    async def _search(self, query: SearchQuery) -> RawSearch:
        body: dict[str, Any] = {
            "q": query.q,
            "gl": query.gl,
            "hl": query.hl,
            "num": query.num,
            "page": query.page,
            # Autocorrect rewrites the query server-side. For a dork built out of exact
            # community names and `inurl:` fragments that is a silent change to what we
            # asked for — and we would be billed for the rewritten query, not ours.
            "autocorrect": False,
            **self.config.extra_params,
        }
        if query.tbs:
            body["tbs"] = query.tbs

        payload = await self.post_json(
            self._endpoint,
            json_body=body,
            headers={"X-API-KEY": self._api_key or "", "Content-Type": "application/json"},
        )
        if not isinstance(payload, dict):
            raise ProviderError(
                f"{self.name} returned {type(payload).__name__} at the top level, expected "
                "an object",
                provider=self.name,
            )

        organic = payload.get("organic")
        if organic is None:
            # A query with genuinely no matches omits `organic` entirely. That is an empty
            # result, not a failure — distinguishing them is what the key check above is
            # for.
            organic = []
        if not isinstance(organic, list):
            raise ProviderError(
                f"{self.name} returned a non-list `organic` field",
                provider=self.name,
            )

        results: list[SerpResult] = []
        for index, row in enumerate(organic, start=1):
            if not isinstance(row, dict):
                continue
            link = row.get("link")
            if not isinstance(link, str) or not link.strip():
                continue
            results.append(
                SerpResult(
                    url=link.strip(),
                    title=str(row.get("title") or "")[:1000],
                    snippet=(str(row["snippet"])[:4000] if row.get("snippet") else None),
                    # `position` is Serper's own ranking within the page; falling back to
                    # the enumeration index keeps the field meaningful if it is absent.
                    position=int(row.get("position") or index),
                    page=query.page,
                    published_at=(str(row["date"])[:100] if row.get("date") else None),
                    template_id=query.template_id,
                )
            )

        credits = payload.get("credits")
        return RawSearch(
            results=results,
            credits=int(credits) if isinstance(credits, int) else None,
            raw=payload,
        )

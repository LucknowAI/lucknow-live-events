"""The web-search port.

Interface only — imports `contracts` and nothing else. A module that depends on this file
cannot tell whether Serper, DataForSEO, Tavily or a recorded fixture answered, which is
the point (ADR-014, ADR-018).

One method wide, like the LLM port. Everything discovery asks a search engine is the same
shape: *here is a query string and a page number, give me the organic results.* Answer
boxes, knowledge panels, images, news verticals and "AI overviews" are all absent from the
contract because no phase needs them; adding one later is a new method, not a rewrite.

Grounded-search products (Gemini grounding, ADK `google_search`) deliberately cannot sit
behind this port — their terms prohibit building a database out of the results (ADR-024).
The port's existence is what makes that a configuration constraint instead of a hope.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from v1.contracts.search import SearchCapabilities, SearchQuery, SearchResponse


@runtime_checkable
class SearchProvider(Protocol):
    """One configured way to turn a query into organic results."""

    name: str
    """The config provider name, e.g. `serper`. Used in logs, accounting and failover."""

    adapter: str
    """Which adapter implementation this is, e.g. `serper`, `dataforseo`, `fixture`."""

    capabilities: SearchCapabilities
    """What this provider can actually do — read *before* a page is requested."""

    async def search(self, query: SearchQuery) -> SearchResponse:
        """Return one page of organic results, with its cost attached.

        Raises:
            v1.contracts.errors.SearchUnsupported: the provider cannot serve this query
                shape (pagination it lacks, operators it ignores). Never an empty list —
                an empty list is indistinguishable from "nothing matched".
            v1.contracts.errors.RateLimited: 429 or a quota error. The failover trigger.
            v1.contracts.errors.ProviderError: transport, auth, timeout or malformed body.
            v1.contracts.errors.BudgetExceeded: the SERP spend cap blocked the query.
        """
        ...

    async def aclose(self) -> None:
        """Release transport resources. Safe to call more than once."""
        ...

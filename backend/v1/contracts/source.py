"""Source vocabulary: how expensive a source is to read, and what kind it is.

The capability ladder (`08` / ADR-020) is the reason this exists as an enum rather than a
comment. A source declares the cheapest tier it can be read at, and the engine always
prefers the cheapest — an official JSON API is not merely nicer than a headless browser,
it removes extraction, its cost, and its failure modes entirely.

Phase 2 only *enumerates* through T0 and T1. T2 and T3 are declared here because a source
list that cannot express "this one needs a browser" would have to be rewritten in Phase 3.
"""

from __future__ import annotations

from enum import StrEnum


class SourceTier(StrEnum):
    """Cheapest way this source can be read. Ordered cheapest → most expensive."""

    T0_API = "T0"
    """Official or public JSON API. Structured, near-zero cost, strong schema."""

    T1_FEED = "T1"
    """Sitemap, RSS or iCal feed. URLs and timestamps only, near-zero cost."""

    T2_STATIC = "T2"
    """Static HTML with embedded metadata (JSON-LD / microdata / OpenGraph)."""

    T3_BROWSER = "T3"
    """Headless browser. Last resort — expensive, fragile, and it still needs an LLM."""


ENUMERABLE_TIERS = frozenset({SourceTier.T0_API, SourceTier.T1_FEED})
"""Tiers a Phase 2 focused source may declare. T2/T3 sources are discovered *by* the
general strategy and fetched in Phase 3; they have nothing to enumerate."""


class SourceKind(StrEnum):
    """What the enumerator returns, which decides how much downstream work is skippable."""

    STRUCTURED = "structured"
    """Full event records already. Extraction can pass them through at confidence 1.0."""

    URL_LIST = "url_list"
    """URLs only. Every one still has to be fetched and extracted."""

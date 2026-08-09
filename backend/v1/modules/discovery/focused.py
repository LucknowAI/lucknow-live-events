"""Focused discovery — known sources, zero search spend.

This is the strategy that matters most and costs least, which is why it runs first and why
it was built first. `08 §4` calls it *"invert ingestion: push before pull"*: one request to
a platform API returns every event a community has, already structured, and no SERP credit
was spent to find any of them.

The module is deliberately thin. All the platform knowledge lives in the enumerators
behind `ports/source.py`; what happens here is the part that is the same for every source:
normalize the URL, classify it, ask the store which ones are new, and report per source.

**One enumerator failing must not fail the run.** If Commudle's sitemap is down, the GDG
API results are still worth having — so each source is caught individually and its error is
recorded on the report rather than raised. That is the opposite of the rule for a *single*
source's result (where a partial answer presented as complete is the dangerous thing), and
the difference is that here we can say precisely which source is missing.
"""

from __future__ import annotations

from v1.contracts.discovery import DiscoveredItem, SourceOutcome
from v1.contracts.errors import EngineError
from v1.contracts.verdicts import UrlClassification, UrlVerdict
from v1.modules.discovery.store import DiscoveryStore
from v1.modules.discovery.url_filter import UrlClassifier
from v1.modules.discovery.urls import normalize_url, url_hash
from v1.platform.logging import get_logger
from v1.ports.source import SourceEnumerator

logger = get_logger("v1.discovery.focused")


class FocusedDiscovery:
    """Enumerates configured sources and returns classified, novelty-marked candidates."""

    def __init__(self, *, classifier: UrlClassifier, store: DiscoveryStore) -> None:
        self._classifier = classifier
        self._store = store

    async def run(
        self, enumerators: list[SourceEnumerator]
    ) -> tuple[list[DiscoveredItem], list[UrlClassification], list[SourceOutcome]]:
        all_items: list[DiscoveredItem] = []
        all_classifications: list[UrlClassification] = []
        outcomes: list[SourceOutcome] = []

        for enumerator in enumerators:
            try:
                raw_items = await enumerator.enumerate_items()
            except EngineError as exc:
                # Recorded, not raised. See the module docstring.
                logger.error(
                    "focused_source_failed",
                    source=enumerator.source_id,
                    adapter=enumerator.adapter,
                    code=exc.code,
                    error=exc.message,
                )
                outcomes.append(
                    SourceOutcome(
                        source_id=enumerator.source_id,
                        adapter=enumerator.adapter,
                        tier=enumerator.tier,
                        error=f"{exc.code}: {exc.message}",
                    )
                )
                continue

            items, classifications = self._classify(raw_items)
            novel = await self._mark_novelty(items)
            all_items.extend(novel)
            all_classifications.extend(classifications)
            outcomes.append(
                SourceOutcome(
                    source_id=enumerator.source_id,
                    adapter=enumerator.adapter,
                    tier=enumerator.tier,
                    items_enumerated=len(novel),
                    novel_urls=sum(1 for item in novel if item.first_seen),
                )
            )

        return all_items, all_classifications, outcomes

    def _classify(
        self, raw_items: list[DiscoveredItem]
    ) -> tuple[list[DiscoveredItem], list[UrlClassification]]:
        """Normalize and classify. A source's own URLs still go through the rules.

        Trusting a T0 API's URLs unconditionally would be tempting and wrong: a Bevy
        chapter page and a Bevy event page come from the same feed, and only one of them is
        a single event.
        """
        items: list[DiscoveredItem] = []
        classifications: list[UrlClassification] = []

        for item in raw_items:
            classification = self._classifier.classify(item.raw_url)
            classifications.append(classification)
            if classification.verdict is UrlVerdict.IRRELEVANT:
                continue
            try:
                normalized = normalize_url(item.raw_url)
            except ValueError:
                continue
            items.append(
                item.model_copy(
                    update={
                        "url": normalized,
                        "verdict": classification.verdict,
                        "verdict_reason": classification.reason,
                    }
                )
            )
        return items, classifications

    async def _mark_novelty(self, items: list[DiscoveredItem]) -> list[DiscoveredItem]:
        hashes = [url_hash(item.url) for item in items]
        novel = await self._store.novel_hashes(hashes)
        return [
            item.model_copy(update={"first_seen": digest in novel})
            for item, digest in zip(items, hashes, strict=True)
        ]

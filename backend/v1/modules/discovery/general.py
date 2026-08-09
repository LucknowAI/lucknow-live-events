"""General discovery — dork templates through the search chain.

The supplement, not the primary. It costs money per query, so every rule here exists to
make sure a query is only issued when it can pay for itself:

1. An **exhausted** query is skipped without issuing anything.
2. A query resumes at the cursor's page rather than page 1.
3. Descent stops at the **novelty floor** (`pagination.py`).
4. `max_pages` per template is a hard ceiling regardless.
5. A provider that cannot serve the query shape is skipped with a reason, not paid.
6. The **budget cap** ends general discovery for the run — it does not fall through to
   another paid provider, because that is spending around the cap, not under it.

The searching itself goes through the failover chain, which is typed against the port. This
module has never heard of Serper.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from v1.config.schema import DiscoveryConfig, SearchDefaults, TemplateConfig
from v1.contracts.discovery import DiscoveredItem, StrategyKind, TemplateOutcome
from v1.contracts.errors import (
    AllProvidersExhausted,
    BudgetExceeded,
    ConfigError,
    EngineError,
)
from v1.contracts.search import SearchQuery, SearchResponse
from v1.contracts.source import SourceTier
from v1.contracts.verdicts import UrlClassification, UrlVerdict
from v1.modules.discovery.pagination import (
    StopReason,
    next_cursor_page,
    score_page,
    should_mark_exhausted,
)
from v1.modules.discovery.store import CHAIN_SCOPE, DiscoveryStore, QueryCursor
from v1.modules.discovery.templates import render
from v1.modules.discovery.url_filter import UrlClassifier
from v1.modules.discovery.urls import normalize_url, url_hash
from v1.platform.logging import get_logger

logger = get_logger("v1.discovery.general")

GENERAL_TIER = SourceTier.T2_STATIC
"""A URL found by search has no declared capability — nobody told us it has an API or a
feed. T2 (static HTML with embedded metadata) is the cheapest tier that could plausibly
read an arbitrary page, and Phase 3's ladder is what actually decides, falling through to
T3 when T2 comes back empty."""


class BudgetHalted(Exception):
    """Internal signal: the SERP cap ended general discovery for this run."""


class GeneralDiscovery:
    """Walks the template bank through the search chain, novelty-floor bounded."""

    def __init__(
        self,
        *,
        chain: object,
        classifier: UrlClassifier,
        store: DiscoveryStore,
        config: DiscoveryConfig,
        defaults: SearchDefaults,
        dry_run: bool,
    ) -> None:
        # `chain` is a `SearchFailoverChain`, held as `object` only to keep the type
        # annotation from dragging `platform.quota` into every caller's import graph. It is
        # used solely through `.search()` and `.drain_switches()`.
        self._chain = chain
        self._classifier = classifier
        self._store = store
        self._config = config
        self._defaults = defaults
        self._dry_run = dry_run

    async def run(
        self, templates: list[TemplateConfig], context: dict[str, str]
    ) -> tuple[list[DiscoveredItem], list[UrlClassification], list[TemplateOutcome]]:
        items: list[DiscoveredItem] = []
        classifications: list[UrlClassification] = []
        outcomes: list[TemplateOutcome] = []

        for template in templates:
            try:
                outcome, template_items, template_classifications = await self._walk(
                    template, context
                )
            except BudgetHalted:
                logger.warning(
                    "general_discovery_halted",
                    reason="search_budget_exceeded",
                    templates_remaining=len(templates) - len(outcomes),
                )
                break
            except ConfigError as exc:
                # A template that cannot render is a config bug, and the rest of the bank
                # is still worth running. Recorded so it is fixed rather than tolerated.
                logger.error("template_render_failed", template=template.id, error=exc.message)
                outcomes.append(
                    TemplateOutcome(
                        template_id=template.id,
                        rendered_query="",
                        provider="",
                        stopped_because=f"render_failed: {exc.message}"[:200],
                    )
                )
                continue

            outcomes.append(outcome)
            items.extend(template_items)
            classifications.extend(template_classifications)

        return items, classifications, outcomes

    # ------------------------------------------------------------------------ walk

    async def _walk(
        self, template: TemplateConfig, context: dict[str, str]
    ) -> tuple[TemplateOutcome, list[DiscoveredItem], list[UrlClassification]]:
        rendered = render(template, context)
        probe = SearchQuery(
            q=rendered,
            template_id=template.id,
            requires_operators=template.requires_operators,
        )
        # Scoped to the query, not to `chain[0]`. The cursor records how deep we have
        # gone into this query's result space, and that depth survives a failover — the
        # provider that answers is incidental to it, and is recorded separately below.
        cursor = await self._store.get_cursor(
            template_id=template.id, query_hash=probe.query_hash, provider=CHAIN_SCOPE
        )
        cursor.rendered_query = rendered
        provider_name = cursor.last_provider or self._chain.provider_names[0]  # type: ignore[attr-defined]

        if cursor.is_exhausted(reset_after_hours=self._config.exhausted_reset_hours):
            logger.info(
                "template_skipped_exhausted",
                template=template.id,
                exhausted_at=str(cursor.exhausted_at),
            )
            return (
                TemplateOutcome(
                    template_id=template.id,
                    rendered_query=rendered,
                    provider=provider_name,
                    stopped_because=StopReason.EXHAUSTED,
                ),
                [],
                [],
            )

        page = max(cursor.next_page, 1)
        pages_fetched = 0
        results_seen = 0
        novel_total = 0
        cost = Decimal(0)
        cache_hits = 0
        last_ratio = 0.0
        stop_reason: StopReason | str = StopReason.MAX_PAGES
        used_provider = provider_name

        items: list[DiscoveredItem] = []
        classifications: list[UrlClassification] = []

        while pages_fetched < template.max_pages:
            query = SearchQuery(
                q=rendered,
                page=page,
                num=template.num or self._defaults.num,
                gl=self._defaults.gl,
                hl=self._defaults.hl,
                tbs=template.tbs,
                template_id=template.id,
                requires_operators=template.requires_operators,
            )

            try:
                response: SearchResponse = await self._chain.search(query)  # type: ignore[attr-defined]
            except BudgetExceeded as exc:
                stop_reason = StopReason.BUDGET
                logger.warning("template_stopped_budget", template=template.id, error=exc.message)
                await self._save_cursor(cursor, page, last_ratio)
                raise BudgetHalted from exc
            except AllProvidersExhausted as exc:
                stop_reason = (
                    StopReason.BUDGET
                    if exc.context.get("budget_blocked")
                    else StopReason.PROVIDER_ERROR
                )
                logger.error(
                    "template_stopped_no_provider",
                    template=template.id,
                    attempts=exc.context.get("attempts"),
                )
                if stop_reason is StopReason.BUDGET:
                    await self._save_cursor(cursor, page, last_ratio)
                    raise BudgetHalted from exc
                break
            except EngineError as exc:
                # Anything else a provider can raise that the chain does not treat as a
                # failover trigger — a missing fixture, a malformed body, a schema the
                # adapter could not read. One template failing must not take the other nine
                # with it, so the reason lands on the report instead of the run aborting.
                # (Focused discovery applies the same rule per source.)
                stop_reason = f"{StopReason.PROVIDER_ERROR}: {exc.code}"
                logger.error(
                    "template_stopped_error",
                    template=template.id,
                    code=exc.code,
                    error=exc.message,
                )
                break

            used_provider = response.provider
            cursor.last_provider = response.provider
            cost += response.cost_usd
            if response.cached:
                cache_hits += 1

            page_items, page_classifications = self._classify(response, template)
            classifications.extend(page_classifications)

            hashes = [url_hash(item.url) for item in page_items]
            novel = await self._store.novel_hashes(hashes)
            page_items = [
                item.model_copy(update={"first_seen": digest in novel})
                for item, digest in zip(page_items, hashes, strict=True)
            ]
            items.extend(page_items)

            results_seen += response.result_count
            novel_on_page = sum(1 for item in page_items if item.first_seen)
            novel_total += novel_on_page
            pages_fetched += 1

            verdict = score_page(
                results_on_page=response.result_count,
                novel_on_page=novel_on_page,
                novelty_floor=self._config.novelty_floor,
            )
            last_ratio = verdict.novelty_ratio
            if not verdict.should_continue:
                stop_reason = verdict.reason or StopReason.NOVELTY_FLOOR
                break

            # A page counts as seen even when it was served from cache, but the novelty
            # bookkeeping must not treat a cached repeat as fresh evidence of productivity
            # — it is the same page. `first_seen` already handles that: the URLs are in the
            # store, so they are not novel.
            page += 1

        await self._save_cursor(cursor, page, last_ratio)

        return (
            TemplateOutcome(
                template_id=template.id,
                rendered_query=rendered,
                provider=used_provider,
                pages_fetched=pages_fetched,
                results_seen=results_seen,
                novel_urls=novel_total,
                novelty_ratio=round(last_ratio, 3),
                stopped_because=str(stop_reason),
                cost_usd=cost,
                cache_hits=cache_hits,
            ),
            items,
            classifications,
        )

    async def _save_cursor(self, cursor: QueryCursor, page: int, ratio: float) -> None:
        """Persist where the next run should resume. Never in a dry run."""
        if self._dry_run:
            return

        previous = cursor.novel_ratio
        cursor.max_page_seen = max(cursor.max_page_seen, page)
        cursor.next_page = next_cursor_page(
            last_page=page, novelty_ratio=ratio, novelty_floor=self._config.novelty_floor
        )
        if should_mark_exhausted(novelty_ratio=ratio, previous_ratio=previous):
            cursor.exhausted_at = datetime.now(UTC)
        elif ratio > 0:
            cursor.exhausted_at = None
        cursor.novel_ratio = ratio
        cursor.last_run_at = datetime.now(UTC)
        await self._store.save_cursor(cursor)

    def _classify(
        self, response: SearchResponse, template: TemplateConfig
    ) -> tuple[list[DiscoveredItem], list[UrlClassification]]:
        items: list[DiscoveredItem] = []
        classifications: list[UrlClassification] = []

        for result in response.results:
            classification = self._classifier.classify(result.url)
            classifications.append(classification)
            if classification.verdict is UrlVerdict.IRRELEVANT:
                continue
            try:
                normalized = normalize_url(result.url)
            except ValueError:
                continue
            items.append(
                DiscoveredItem(
                    url=normalized,
                    raw_url=result.url,
                    strategy=StrategyKind.GENERAL,
                    origin=template.id,
                    tier=GENERAL_TIER,
                    verdict=classification.verdict,
                    verdict_reason=classification.reason,
                    title=result.title or None,
                    snippet=result.snippet,
                )
            )
        return items, classifications

"""The discovery run: focused first, then general, then one batched triage call.

Order is cost order, and it is not incidental. Focused enumeration is free and covers the
communities that produce most of the real events, so it runs first and always — even when
the search budget is exhausted, when no SERP key is configured, or when every search
provider is down. General discovery is the supplement that runs afterwards if it can.

The batched LLM triage runs **last, once**, over everything both strategies could not
classify by rule. Triaging per strategy would double the calls; triaging per URL would
multiply them by a hundred.

`dry_run` is the difference between `/discovery/preview` and `/discovery/run`, and it is
honoured all the way down: preview uses an in-memory store, so it renders templates, issues
real queries and classifies real results while writing **no** `discovered_url` row and
moving **no** pagination cursor. A tuning tool that changes the state it is tuning gives a
different answer every time you look at it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter

from v1.config.schema import EngineConfig, TemplateConfig
from v1.contracts.discovery import (
    DiscoveredItem,
    DiscoveryReport,
    SourceOutcome,
    StrategyKind,
    TemplateOutcome,
    UrlTriageBatch,
)
from v1.contracts.errors import EngineError
from v1.contracts.verdicts import UrlClassification, UrlVerdict
from v1.modules.discovery.focused import FocusedDiscovery
from v1.modules.discovery.general import GeneralDiscovery
from v1.modules.discovery.store import DiscoveryStore
from v1.modules.discovery.templates import build_context
from v1.modules.discovery.url_filter import (
    TRIAGE_SYSTEM,
    TRIAGE_USER,
    UrlClassifier,
    apply_triage,
    build_triage_content,
)
from v1.platform.logging import get_logger
from v1.ports.llm import LLMProvider
from v1.ports.source import SourceEnumerator

logger = get_logger("v1.discovery.runner")


class DiscoveryRunner:
    """One discovery run, either strategy or both."""

    def __init__(
        self,
        *,
        config: EngineConfig,
        classifier: UrlClassifier,
        store: DiscoveryStore,
        enumerators: list[SourceEnumerator],
        chain: object | None = None,
        triage_llm: LLMProvider | None = None,
        dry_run: bool = True,
    ) -> None:
        self._config = config
        self._classifier = classifier
        self._store = store
        self._enumerators = enumerators
        self._chain = chain
        self._triage_llm = triage_llm
        self._dry_run = dry_run

    async def run(
        self,
        *,
        strategy: StrategyKind | None = None,
        templates: list[TemplateConfig] | None = None,
        now: datetime | None = None,
    ) -> DiscoveryReport:
        started_at = now or datetime.now(UTC)
        clock = perf_counter()

        items: list[DiscoveredItem] = []
        classifications: list[UrlClassification] = []
        sources: list[SourceOutcome] = []
        template_outcomes: list[TemplateOutcome] = []
        errors: list[str] = []
        switches: list[str] = []

        if strategy in (None, StrategyKind.FOCUSED):
            focused = FocusedDiscovery(classifier=self._classifier, store=self._store)
            focused_items, focused_classifications, sources = await focused.run(self._enumerators)
            items.extend(focused_items)
            classifications.extend(focused_classifications)

        if strategy in (None, StrategyKind.GENERAL):
            (
                general_items,
                general_classifications,
                template_outcomes,
                errors_general,
            ) = await self._run_general(templates or [], started_at)
            items.extend(general_items)
            classifications.extend(general_classifications)
            errors.extend(errors_general)
            if self._chain is not None:
                switches = self._chain.drain_switches()  # type: ignore[attr-defined]

        items, classifications, triage_calls, llm_cost = await self._triage(items, classifications)

        if not self._dry_run:
            await self._store.record_items(items)

        return DiscoveryReport(
            strategy=strategy,
            started_at=started_at,
            duration_ms=int((perf_counter() - clock) * 1000),
            dry_run=self._dry_run,
            items=items,
            classifications=classifications,
            templates=template_outcomes,
            sources=sources,
            queries_issued=sum(outcome.pages_fetched for outcome in template_outcomes),
            cache_hits=sum(outcome.cache_hits for outcome in template_outcomes),
            search_cost_usd=sum(
                (outcome.cost_usd for outcome in template_outcomes), start=Decimal(0)
            ),
            llm_cost_usd=llm_cost,
            triage_calls=triage_calls,
            provider_switches=switches,
            errors=errors,
        )

    # -------------------------------------------------------------------- general

    async def _run_general(
        self, templates: list[TemplateConfig], now: datetime
    ) -> tuple[list[DiscoveredItem], list[UrlClassification], list[TemplateOutcome], list[str]]:
        discovery = self._config.discovery
        search = self._config.search
        if discovery is None or search is None or self._chain is None:
            # Not an error. A focused-only instance is a legitimate zero-spend deployment,
            # and so is a run whose SERP keys are absent — the focused half already ran.
            return [], [], [], ["general discovery unavailable: no search chain configured"]

        general = GeneralDiscovery(
            chain=self._chain,
            classifier=self._classifier,
            store=self._store,
            config=discovery,
            defaults=search.defaults,
            dry_run=self._dry_run,
        )
        context = build_context(
            self._config.locality, timezone=self._config.tenant.timezone, now=now
        )
        items, classifications, outcomes = await general.run(templates, context)
        return items, classifications, outcomes, []

    # --------------------------------------------------------------------- triage

    async def _triage(
        self, items: list[DiscoveredItem], classifications: list[UrlClassification]
    ) -> tuple[list[DiscoveredItem], list[UrlClassification], int, Decimal]:
        """One batched LLM call per `batch_size` unknown URLs, capped per run."""
        discovery = self._config.discovery
        if discovery is None or not discovery.triage.enabled or self._triage_llm is None:
            return items, classifications, 0, Decimal(0)

        unknown_indexes = [
            index for index, item in enumerate(items) if item.verdict is UrlVerdict.UNKNOWN
        ]
        if not unknown_indexes:
            return items, classifications, 0, Decimal(0)

        batch_size = discovery.triage.batch_size
        max_batches = discovery.triage.max_batches
        batches = [
            unknown_indexes[offset : offset + batch_size]
            for offset in range(0, len(unknown_indexes), batch_size)
        ][:max_batches]

        if len(unknown_indexes) > batch_size * max_batches:
            # Stated rather than silently truncated: the URLs beyond the cap stay UNKNOWN,
            # which is a verdict the pipeline can act on, and the number is in the log.
            logger.warning(
                "triage_batch_cap_reached",
                unknown=len(unknown_indexes),
                triaged=batch_size * max_batches,
                max_batches=max_batches,
            )

        resolved = list(items)
        cost = Decimal(0)
        calls = 0

        for batch in batches:
            payload = [(items[index].url, items[index].title) for index in batch]
            try:
                result = await self._triage_llm.complete_structured(
                    system=TRIAGE_SYSTEM,
                    user=TRIAGE_USER,
                    schema=UrlTriageBatch,
                    task=discovery.triage.task,
                    # Titles come from third-party pages, so they are untrusted content and
                    # go through the port's spotlighting rather than into the instruction.
                    untrusted_content=build_triage_content(payload),
                )
            except EngineError as exc:
                # Triage failing leaves URLs UNKNOWN, which is a usable verdict. It must
                # never fail the run — the discovered URLs are still discovered.
                logger.error("triage_call_failed", code=exc.code, error=exc.message)
                break

            calls += 1
            cost += result.cost_usd

            pending = [
                UrlClassification(
                    url=items[index].url,
                    verdict=UrlVerdict.UNKNOWN,
                    reason="queued for batched triage",
                    rule_id="no_match",
                )
                for index in batch
            ]
            triaged = apply_triage(
                pending,
                [(entry.index, entry.verdict, entry.reason) for entry in result.value.verdicts],
            )
            for position, classification in enumerate(triaged):
                item_index = batch[position]
                resolved[item_index] = resolved[item_index].model_copy(
                    update={
                        "verdict": classification.verdict,
                        "verdict_reason": classification.reason,
                    }
                )
                classifications.append(classification)

        return resolved, classifications, calls, cost

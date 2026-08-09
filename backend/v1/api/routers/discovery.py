"""Discovery endpoints — the module's deliverable, not a side effect.

These are how discovery gets inspected without running the whole engine, which is the
testability the plan is built around. They divide cleanly by what they cost and what they
change:

| Endpoint | Spends? | Writes? |
|---|---|---|
| `GET /discovery/providers` · `sources` · `templates` · `state` | no | no |

| `POST /discovery/search` | yes | no |
| `POST /discovery/preview` | yes | **no** |
| `POST /discovery/sources/{id}/enumerate` | no | no |
| `POST /discovery/run` | yes | yes |

`preview` writing nothing is the important one. It is the tuning tool: render the
templates, issue the queries, classify the results, see what would have been kept — while
moving no pagination cursor and inserting no `discovered_url` row. A tuning tool that
mutates the state it is tuning gives a different answer every time you look at it.

**Every endpoint here is behind the `X-Engine-Token` gate**, including the read-only
ones. The reads look harmless and are not: `templates` is the exact query text we send to
a paid API, `state` exposes which queries are productive, and `providers` reports the
month's spend against the cap. Together they are a map of what this instance is doing and
what it costs, and none of it needs to be public for the engine to work. Only `/health`
and `/version` are open.

Endpoints that spend money additionally require `V1_SEARCH_ALLOW_LIVE=1` — a configured
API key is not consent to spend it.

**Not implemented:** rate limiting on the token gate. A stolen or brute-forced token is
still bounded by the monthly cap and the live-spend switch, but there is nothing here that
slows an attacker down, and that belongs with the rest of the job-auth work rather than
being half-built now.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Request
from pydantic import BaseModel, Field

from v1.api.deps import ConfigDep, RegistryDep, SettingsDep, require_engine_token
from v1.config.schema import TemplateConfig, TemplateTier
from v1.contracts.discovery import DiscoveryReport, StrategyKind
from v1.contracts.errors import ConfigError, ProfileUnavailable
from v1.contracts.search import DEFAULT_NUM, SearchQuery, SearchResponse
from v1.models import tenant_uuid
from v1.modules.discovery.runner import DiscoveryRunner
from v1.modules.discovery.store import (
    DiscoveryStore,
    InMemoryDiscoveryStore,
    PostgresDiscoveryStore,
    ReadThroughDiscoveryStore,
)
from v1.modules.discovery.templates import build_context
from v1.modules.discovery.url_filter import UrlClassifier
from v1.platform.logging import get_logger
from v1.providers.search.registry import SearchRegistry
from v1.providers.sources.bevy_api import BevyApiEnumerator, resolve_chapter_id
from v1.providers.sources.registry import SourceRegistry

logger = get_logger("v1.api.discovery")

router = APIRouter(prefix="/discovery", tags=["discovery"])


# --------------------------------------------------------------------------- deps


def get_search_registry(request: Request) -> SearchRegistry:
    registry: SearchRegistry | None = getattr(request.app.state, "search_registry", None)
    if registry is None:
        raise ConfigError(
            "this instance has no `search` section configured, so general discovery is "
            "unavailable. Focused discovery still works and costs nothing"
        )
    return registry


def get_source_registry(request: Request) -> SourceRegistry:
    return request.app.state.source_registry


SearchRegistryDep = Annotated[SearchRegistry, Depends(get_search_registry)]
SourceRegistryDep = Annotated[SourceRegistry, Depends(get_source_registry)]


# ------------------------------------------------------------------------ schemas


class ProviderView(BaseModel):
    name: str
    adapter: str
    in_chain: bool
    chain_position: int | None
    available: bool
    unavailable_reason: str | None
    supports_pagination: bool
    supports_operators: bool
    max_num: int
    index: str
    is_costless: bool
    usd_per_query: Decimal | None
    deep_query_multiplier: Decimal | None
    pricing_source: str | None
    pricing_as_of: str | None
    consecutive_failures: int
    disabled_until: str | None


class ProvidersResponse(BaseModel):
    chain: list[str]
    providers: list[ProviderView]
    budget_monthly_cap_usd: Decimal
    budget_on_exceeded: str
    """The configured *policy*. Distinct from the live verdict below — reporting a state
    under a policy's field name is the exact defect the LLM layer's review caught."""

    budget_verdict: str
    budget_spent_usd: Decimal
    budget_remaining_usd: Decimal
    budget_window_start: str
    budget_ledger: str
    cache_backend: str
    cache_ttl_hours: float
    allow_deep_pages: bool
    allow_live: bool


class SourceView(BaseModel):
    id: str
    name: str | None
    adapter: str
    tier: str
    kind: str
    enabled: bool
    external_ref: str | None
    url: str | None


class TemplateView(BaseModel):
    id: str
    tier: str
    enabled: bool
    max_pages: int
    requires_operators: bool
    query_template: str
    rendered_query: str
    render_error: str | None = None


class TemplatesResponse(BaseModel):
    rendered_at: str
    context: dict[str, str]
    templates: list[TemplateView]


class CursorView(BaseModel):
    template_id: str
    provider: str
    rendered_query: str
    next_page: int
    max_page_seen: int
    novel_ratio: float | None
    exhausted_at: str | None
    last_run_at: str | None


class SearchRequest(BaseModel):
    q: str = Field(min_length=1, max_length=2048)
    provider: str | None = None
    page: int = Field(default=1, ge=1, le=100)
    num: int = Field(default=DEFAULT_NUM, ge=1, le=100)
    gl: str | None = None
    hl: str | None = None
    tbs: str | None = None
    use_chain: bool = True
    """Run through the failover chain (the real behaviour) rather than one provider."""


class RunRequest(BaseModel):
    strategy: StrategyKind | None = None
    tiers: list[TemplateTier] = Field(default_factory=lambda: [TemplateTier.A])
    template_ids: list[str] = Field(default_factory=list, max_length=50)
    """Explicit template ids win over `tiers`. The tuning path: run one template."""

    source_ids: list[str] = Field(default_factory=list, max_length=50)
    triage: bool = True


class ResolveChapterRequest(BaseModel):
    source_id: str
    chapter_slug: str = Field(min_length=1, max_length=200)


# ------------------------------------------------------------------------- routes


@router.get(
    "/providers",
    response_model=ProvidersResponse,
    summary="Search providers",
    dependencies=[Depends(require_engine_token)],
)
async def providers(registry: SearchRegistryDep, settings: SettingsDep) -> ProvidersResponse:
    """Each provider's real capabilities, price, availability and this month's spend.

    Availability names the exact missing environment variable, so "why is discovery not
    using Serper" is answerable without shell access.
    """
    search = registry._search
    status = await registry.budget.status()
    return ProvidersResponse(
        chain=registry.chain_names,
        providers=[
            ProviderView(
                name=info.name,
                adapter=info.adapter,
                in_chain=info.in_chain,
                chain_position=info.chain_position,
                available=info.available,
                unavailable_reason=info.unavailable_reason,
                supports_pagination=info.capabilities.supports_pagination,
                supports_operators=info.capabilities.supports_operators,
                max_num=info.capabilities.max_num,
                index=str(info.capabilities.index),
                is_costless=info.capabilities.is_costless,
                usd_per_query=info.usd_per_query,
                deep_query_multiplier=info.deep_query_multiplier,
                pricing_source=info.pricing_source,
                pricing_as_of=info.pricing_as_of,
                consecutive_failures=info.consecutive_failures,
                disabled_until=info.disabled_until,
            )
            for info in registry.providers()
        ],
        budget_monthly_cap_usd=status.cap_usd,
        budget_on_exceeded=str(search.budget.on_exceeded),
        budget_verdict=str(status.verdict),
        budget_spent_usd=status.spent_usd,
        budget_remaining_usd=status.remaining_usd,
        budget_window_start=status.window_start.isoformat(),
        budget_ledger=str(search.budget.ledger),
        cache_backend=str(search.cache.backend),
        cache_ttl_hours=search.cache.ttl_hours,
        allow_deep_pages=search.allow_deep_pages,
        allow_live=settings.SEARCH_ALLOW_LIVE,
    )


@router.get(
    "/sources",
    response_model=list[SourceView],
    summary="Focused sources",
    dependencies=[Depends(require_engine_token)],
)
async def sources(config: ConfigDep, registry: SourceRegistryDep) -> list[SourceView]:
    """The zero-spend half of discovery: which known sources are configured and enabled."""
    discovery = config.config.discovery
    entries = discovery.sources if discovery is not None else []
    views: list[SourceView] = []
    for source in entries:
        kind = "unknown"
        if source.enabled and source.id in registry.source_ids:
            kind = str(registry.get(source.id).kind)
        views.append(
            SourceView(
                id=source.id,
                name=source.name,
                adapter=source.adapter,
                tier=str(source.tier),
                kind=kind,
                enabled=source.enabled,
                external_ref=source.external_ref,
                url=source.url,
            )
        )
    return views


@router.get(
    "/templates",
    response_model=TemplatesResponse,
    summary="Templates for today",
    dependencies=[Depends(require_engine_token)],
)
async def templates(config: ConfigDep) -> TemplatesResponse:
    """Templates as they render *right now* — the tuning surface.

    A render failure is reported per template rather than raised, so one broken template
    does not hide the other nine.
    """
    now = datetime.now(UTC)
    context = build_context(config.config.locality, timezone=config.config.tenant.timezone, now=now)
    from v1.modules.discovery.templates import render

    views: list[TemplateView] = []
    for template in config.templates.templates:
        rendered, error = "", None
        try:
            rendered = render(template, context)
        except ConfigError as exc:
            error = exc.message
        views.append(
            TemplateView(
                id=template.id,
                tier=str(template.tier),
                enabled=template.enabled,
                max_pages=template.max_pages,
                requires_operators=template.requires_operators,
                query_template=template.query,
                rendered_query=rendered,
                render_error=error,
            )
        )
    return TemplatesResponse(rendered_at=now.isoformat(), context=context, templates=views)


@router.get(
    "/state",
    response_model=list[CursorView],
    summary="Pagination cursors",
    dependencies=[Depends(require_engine_token)],
)
async def state(config: ConfigDep, settings: SettingsDep) -> list[CursorView]:
    """Where each template stands: next page, novelty ratio, whether it is mined out."""
    store = _store_for(config, settings, dry_run=False)
    cursors = await store.cursors()
    return [
        CursorView(
            template_id=cursor.template_id,
            provider=cursor.provider,
            rendered_query=cursor.rendered_query,
            next_page=cursor.next_page,
            max_page_seen=cursor.max_page_seen,
            novel_ratio=cursor.novel_ratio,
            exhausted_at=cursor.exhausted_at.isoformat() if cursor.exhausted_at else None,
            last_run_at=cursor.last_run_at.isoformat() if cursor.last_run_at else None,
        )
        for cursor in cursors
    ]


@router.post(
    "/search",
    response_model=SearchResponse,
    summary="Raw provider passthrough (spends, writes nothing)",
    dependencies=[Depends(require_engine_token)],
)
async def raw_search(
    registry: SearchRegistryDep,
    config: ConfigDep,
    payload: Annotated[SearchRequest, Body()],
) -> SearchResponse:
    """One query, one page, cost reported. Bypasses the cache deliberately.

    This endpoint exists to show what a provider returns *right now*; serving it from a
    six-hour-old cache would defeat the only job it has.
    """
    search = config.config.search
    assert search is not None  # guaranteed by get_search_registry
    query = SearchQuery(
        q=payload.q,
        page=payload.page,
        num=payload.num,
        gl=payload.gl or search.defaults.gl,
        hl=payload.hl or search.defaults.hl,
        tbs=payload.tbs,
        template_id=None,
    )

    if payload.provider and not payload.use_chain:
        return await registry.uncached(payload.provider).search(query)

    chain = registry.build_chain(only=payload.provider)
    return await chain.search(query)


@router.post(
    "/sources/{source_id}/enumerate",
    summary="Dry-run one focused source (free, writes nothing)",
    dependencies=[Depends(require_engine_token)],
)
async def enumerate_source(source_id: str, registry: SourceRegistryDep) -> dict[str, Any]:
    """Enumerate one known source and show exactly what it returned. Costs nothing."""
    enumerator = registry.get(source_id)
    items = await enumerator.enumerate_items()
    return {
        "source_id": source_id,
        "adapter": enumerator.adapter,
        "tier": str(enumerator.tier),
        "kind": str(enumerator.kind),
        "count": len(items),
        "items": [item.model_dump(mode="json") for item in items],
    }


@router.post(
    "/sources/resolve-chapter",
    summary="Resolve a Bevy chapter slug to its numeric id",
    dependencies=[Depends(require_engine_token)],
)
async def resolve_chapter(
    payload: Annotated[ResolveChapterRequest, Body()], registry: SourceRegistryDep
) -> dict[str, str]:
    """Operator tool. The id never changes, so it is resolved once and written into config.

    Necessary because Bevy's `/api/chapter/` endpoint requires authentication while
    `/api/event/` does not — the id has to come out of the chapter page's HTML.
    """
    enumerator = registry.get(payload.source_id)
    if not isinstance(enumerator, BevyApiEnumerator):
        raise ConfigError(
            f"source {payload.source_id!r} is not a bevy_api source; chapter ids only "
            "apply to Bevy-hosted communities"
        )
    chapter_id = await resolve_chapter_id(enumerator, payload.chapter_slug)
    return {"chapter_slug": payload.chapter_slug, "chapter_id": chapter_id}


@router.post(
    "/preview",
    response_model=DiscoveryReport,
    summary="Full run with nothing persisted — the tuning tool",
    dependencies=[Depends(require_engine_token)],
)
async def preview(
    request: Request,
    config: ConfigDep,
    settings: SettingsDep,
    registry: RegistryDep,
    payload: Annotated[RunRequest | None, Body()] = None,
) -> DiscoveryReport:
    return await _run(
        request, config, settings, registry, payload=payload or RunRequest(), dry_run=True
    )


@router.post(
    "/run",
    response_model=DiscoveryReport,
    summary="Full run, persisting discovered URLs and cursors",
    dependencies=[Depends(require_engine_token)],
)
async def run(
    request: Request,
    config: ConfigDep,
    settings: SettingsDep,
    registry: RegistryDep,
    payload: Annotated[RunRequest | None, Body()] = None,
) -> DiscoveryReport:
    return await _run(
        request, config, settings, registry, payload=payload or RunRequest(), dry_run=False
    )


# ------------------------------------------------------------------------ helpers


def _store_for(config: Any, settings: Any, *, dry_run: bool) -> DiscoveryStore:
    """Pick the store for this run.

    | | database configured | no database |
    |---|---|---|
    | `run` | Postgres | in-memory, loudly warned |
    | `preview` | **read-through** — real reads, no writes | in-memory |

    The read-through case is the one that matters. A dry run on a blank in-memory store
    writes nothing (right) but also *reads* nothing (wrong): every URL looks unseen, so
    novelty is always 1.0 and the tuning surface reports that every template is productive.
    """
    if not settings.DATABASE_URL:
        if not dry_run:
            logger.warning(
                "discovery_store_not_durable",
                detail="V1_DATABASE_URL is unset, so this run records nothing. Novelty "
                "and pagination will restart from empty on the next run",
            )
        return InMemoryDiscoveryStore()

    durable = PostgresDiscoveryStore(tenant_uuid(config.config.tenant.slug))
    return ReadThroughDiscoveryStore(durable) if dry_run else durable


def _select_templates(config: Any, payload: RunRequest) -> list[TemplateConfig]:
    bank = config.templates
    if payload.template_ids:
        wanted = set(payload.template_ids)
        return [t for t in bank.templates if t.id in wanted and t.enabled]
    return bank.by_tier(set(payload.tiers))


async def _run(
    request: Request,
    config: Any,
    settings: Any,
    llm_registry: Any,
    *,
    payload: RunRequest,
    dry_run: bool,
) -> DiscoveryReport:
    discovery = config.config.discovery
    if discovery is None:
        raise ConfigError("this instance has no `discovery` section configured")

    search_registry: SearchRegistry | None = getattr(request.app.state, "search_registry", None)
    source_registry: SourceRegistry = request.app.state.source_registry

    enumerators = []
    if payload.strategy in (None, StrategyKind.FOCUSED):
        if payload.source_ids:
            enumerators = [source_registry.get(source_id) for source_id in payload.source_ids]
        else:
            enumerators = source_registry.all()

    chain = None
    if payload.strategy in (None, StrategyKind.GENERAL) and search_registry is not None:
        try:
            chain = search_registry.build_chain()
        except ConfigError as exc:
            # No usable SERP key. Focused discovery is free and still worth running, so
            # this degrades rather than fails — with the reason on the report.
            logger.warning("search_chain_unavailable", reason=exc.message)

    triage_llm = None
    if payload.triage and discovery.triage.enabled:
        try:
            triage_llm = llm_registry.for_task(discovery.triage.task)
        except (ConfigError, ProfileUnavailable) as exc:
            logger.warning("triage_llm_unavailable", reason=str(exc))

    runner = DiscoveryRunner(
        config=config.config,
        classifier=UrlClassifier(discovery.url_rules),
        store=_store_for(config, settings, dry_run=dry_run),
        enumerators=enumerators,  # type: ignore[arg-type]
        chain=chain,
        triage_llm=triage_llm,
        dry_run=dry_run,
    )
    return await runner.run(strategy=payload.strategy, templates=_select_templates(config, payload))

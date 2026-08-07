"""The V1 engine's own ASGI app.

    cd backend && uvicorn v1.api.app:app --port 8100 --reload

Standalone by design (ADR-023): the V0 app keeps running untouched and this is not mounted
into it. Cutover is a later, flagged decision.

Startup is fail-fast and loud. The app refuses to start when:

* the config file is missing, malformed, or references a profile/secret it cannot resolve;
* a registered response schema would be rejected by an adapter;
* we are not in development and no `V1_ENGINE_TOKEN` is set — the gated endpoints spend
  money, and an open one in a deployed environment is a funded anonymous prompt relay;
* we are not in development and the budget ledger is `memory` — a spend cap that a restart
  clears is not a cap.

Refusing to boot is the point. A misconfigured engine that starts anyway is a bill.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from v1 import __version__
from v1.api.routers import llm as llm_router
from v1.api.routers import meta as meta_router
from v1.config.loader import LoadedConfig, load_config
from v1.config.schema import LedgerKind
from v1.config.settings import V1Settings, get_settings
from v1.contracts.errors import ConfigError, EngineError
from v1.platform.db import dispose_engine
from v1.platform.logging import get_logger, setup_logging
from v1.platform.middleware import CorrelationLoggingMiddleware
from v1.providers.llm.registry import LLMRegistry

logger = get_logger("v1.api")

DESCRIPTION = """
Ingestion Engine V1 — Phase 1: the LLM provider layer.

Configured entirely by `backend/v1/config/instance.yaml`. Switch every model call to a
local model by changing one line (`llm.default`); switch one task by changing one line
under `llm.tasks`.
"""


def validate_runtime(settings: V1Settings, config: LoadedConfig) -> None:
    """Environment-dependent safety checks that config validation alone cannot make."""
    if settings.is_development:
        if not settings.ENGINE_TOKEN:
            logger.warning(
                "engine_token_unset",
                detail="gated endpoints are unauthenticated in development",
            )
        return

    problems: list[str] = []
    if not settings.ENGINE_TOKEN:
        problems.append(
            "V1_ENGINE_TOKEN is unset. The /llm endpoints spend money against provider "
            "keys; they must not be reachable unauthenticated outside development."
        )
    if config.config.llm.budget.ledger is LedgerKind.MEMORY:
        problems.append(
            "llm.budget.ledger is `memory`. A spend cap held in process memory resets on "
            "every restart. Set V1_LLM_BUDGET_LEDGER=postgres (and V1_DATABASE_URL)."
        )
    if config.config.llm.budget.ledger is LedgerKind.POSTGRES and not settings.DATABASE_URL:
        problems.append("llm.budget.ledger is `postgres` but V1_DATABASE_URL is unset.")

    if problems:
        raise ConfigError(
            "engine cannot start in environment "
            f"{settings.ENVIRONMENT!r}:\n  - " + "\n  - ".join(problems)
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings, force=True)

    config = load_config(settings=settings)
    validate_runtime(settings, config)
    registry = LLMRegistry.from_config(config)

    app.state.settings = settings
    app.state.config = config
    app.state.llm_registry = registry

    # The instance's identity comes from config, never from a literal in code.
    app.title = f"{config.config.tenant.name} — Ingestion Engine V1"
    app.openapi_schema = None  # force regeneration with the resolved title

    if config.config.llm.budget.ledger is LedgerKind.POSTGRES:
        from v1.platform.bootstrap import ensure_tenant

        await ensure_tenant(config.config.tenant)

    logger.info(
        "engine_started",
        version=__version__,
        environment=settings.ENVIRONMENT,
        tenant=config.config.tenant.slug,
        config_path=str(config.path),
        default_profile=registry.default_profile,
    )
    try:
        yield
    finally:
        await registry.aclose()
        await dispose_engine()
        logger.info("engine_stopped", version=__version__)


def create_app() -> FastAPI:
    app = FastAPI(
        # Product-neutral: the instance's name is config, not code (ADR-013). The lifespan
        # prefixes the tenant name once the config is loaded.
        title="Ingestion Engine V1",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.add_middleware(CorrelationLoggingMiddleware)
    app.include_router(meta_router.router)
    app.include_router(llm_router.router)

    @app.exception_handler(EngineError)
    async def engine_error_handler(_request: Request, exc: EngineError) -> JSONResponse:
        """One place maps typed engine errors to status codes.

        The client always learns *which* failure happened (`code`) rather than a generic
        500, and the context travels with it — so "why did extraction return nothing" is
        answerable from the response alone.
        """
        logger.warning("engine_error", code=exc.code, message=exc.message, **exc.context)
        return JSONResponse(status_code=exc.http_status, content={"error": exc.to_dict()})

    return app


app = create_app()

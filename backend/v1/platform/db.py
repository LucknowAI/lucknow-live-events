"""Async engine/session factory for the V1 engine, scoped to schema `engine_v1`.

Isolation from the V0 pipeline (ADR-023): same database, own schema, own Alembic version
table. `DROP SCHEMA engine_v1 CASCADE` resets the engine and cannot touch V0 tables.

The engine is created lazily. The whole LLM layer runs with `ledger: memory` and no
database at all, so importing this module must never require `V1_DATABASE_URL` to be set.

Row-level security: every session sets `engine_v1.tenant_id` as a local GUC, which is
what the RLS policies compare against. One tenant today; the seam is what matters.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from v1.config.settings import V1Settings, get_settings
from v1.contracts.errors import ConfigError

SCHEMA = "engine_v1"
TENANT_GUC = "engine_v1.tenant_id"

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _normalise_dsn(url: str) -> tuple[str, dict]:
    """Strip psycopg2-only query params asyncpg cannot parse, and pass real SSL instead.

    Same problem the V0 code hit: `?sslmode=require` is a libpq convention and asyncpg
    does not honour it as a URL parameter.
    """
    connect_args: dict = {}
    if "sslmode" in url:
        for token in (
            "?sslmode=require",
            "&sslmode=require",
            "?sslmode=verify-full",
            "&sslmode=verify-full",
        ):
            url = url.replace(token, "")
        connect_args["ssl"] = ssl.create_default_context()
    return url, connect_args


def get_engine(settings: V1Settings | None = None) -> AsyncEngine:
    global _engine
    if _engine is not None:
        return _engine

    settings = settings or get_settings()
    if not settings.DATABASE_URL:
        raise ConfigError(
            "V1_DATABASE_URL is not set, but a database-backed component was requested. "
            "Set it, or run with V1_LLM_BUDGET_LEDGER=memory."
        )

    url, connect_args = _normalise_dsn(settings.DATABASE_URL)
    # NullPool: this process may be short-lived (a job run) or serverless. A warm pool is
    # a later optimisation with measurements behind it, not a default.
    _engine = create_async_engine(url, connect_args=connect_args, poolclass=NullPool)
    return _engine


def get_sessionmaker(settings: V1Settings | None = None) -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(settings), class_=AsyncSession, expire_on_commit=False
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope(
    tenant_id: str | None = None, *, settings: V1Settings | None = None
) -> AsyncIterator[AsyncSession]:
    """A session with the tenant GUC set, committed on success, rolled back on error."""
    factory = get_sessionmaker(settings)
    async with factory() as session:
        try:
            if tenant_id is not None:
                await session.execute(
                    text(f"SELECT set_config('{TENANT_GUC}', :tenant_id, true)"),
                    {"tenant_id": str(tenant_id)},
                )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close pooled connections. Called on app shutdown and between test modules."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None

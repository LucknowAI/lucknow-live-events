from __future__ import annotations

import os
import ssl
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.core.config import settings


def _make_engine():
    url = settings.DATABASE_URL
    connect_args: dict = {}

    # asyncpg does not reliably parse `sslmode=require` as a URL query param
    # (that's a psycopg2 convention). Strip it and pass an explicit SSLContext so
    # the behaviour is consistent across all SQLAlchemy / asyncpg versions.
    if "sslmode" in url:
        url = (
            url
            .replace("?sslmode=require", "")
            .replace("&sslmode=require", "")
            .replace("?sslmode=verify-full", "")
            .replace("&sslmode=verify-full", "")
        )
        ctx = ssl.create_default_context()
        connect_args["ssl"] = ctx

    # Use NullPool on every cloud environment (Render, Vercel, Cloud Run).
    # Local Docker dev sets DOCKER_ENV=1, which enables the default pool
    # for warm connections across requests.
    is_docker = os.getenv("DOCKER_ENV") == "1"
    pool_kwargs: dict = {"pool_pre_ping": True} if is_docker else {"poolclass": NullPool}

    return create_async_engine(url, connect_args=connect_args, **pool_kwargs)


engine = _make_engine()
SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session

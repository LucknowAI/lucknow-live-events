from __future__ import annotations

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api.core.config import settings

# On Vercel (serverless) each invocation may be a new process, so we use
# NullPool to prevent connection leaks. On long-running servers (Docker/Railway)
# we use the default pool for performance.
_pool_kwargs = {"poolclass": NullPool} if os.getenv("VERCEL") == "1" else {"pool_pre_ping": True}

engine = create_async_engine(settings.DATABASE_URL, **_pool_kwargs)
SessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session

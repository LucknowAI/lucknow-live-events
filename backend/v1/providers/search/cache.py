"""SERP response cache, keyed `sha256(query|provider|page|num|gl|hl|tbs)`.

This is a first-class part of the search module, not an optimisation bolted on later, for
one specific reason: **tier-A dork templates re-render to the same query string every
run.** Without a cache, running general discovery twice a day means paying twice a day for
the identical page-1 result of `site:gdg.community.dev inurl:/events/details/ …`. With a
6-hour TTL, the second run of the day is free.

It is also worth stating what makes this legal here and not everywhere. Caching *SERP API*
results is ordinary use of a product sold for exactly that. Caching *grounded* results from
Gemini or ADK's `google_search` is prohibited by their terms — which is one of the reasons
those products are not behind this port at all (ADR-024).

Two backends, and the choice is not cosmetic:

* **`postgres`** — the only one that does anything in production. A discovery run is a
  short-lived cron process: it starts, issues its queries, exits. An in-process cache in
  that shape has a 0% hit rate.
* **`memory`** — dev, tests, and a long-lived API process where a burst of `/discovery/preview`
  calls does benefit from it. `v1.api.app.validate_runtime` refuses to start a
  non-development environment on it, for the same reason it refuses the in-memory spend
  ledger: a control that a restart erases is not a control.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import delete, select

from v1.contracts.search import SearchResponse
from v1.platform.logging import get_logger

logger = get_logger("v1.search.cache")


class SerpCache(Protocol):
    async def get(self, key: str) -> SearchResponse | None:
        """Return a cached response, or None. Must never raise for a storage problem —
        a broken cache degrades to a cache miss, it does not fail the run."""
        ...

    async def put(self, key: str, response: SearchResponse) -> None: ...


@dataclass(slots=True)
class _Entry:
    response: SearchResponse
    expires_at: float


class InMemorySerpCache:
    """Process-local cache with a size bound and a monotonic-clock TTL."""

    def __init__(self, *, ttl_hours: float, max_entries: int = 5000) -> None:
        self._ttl_s = ttl_hours * 3600
        self._max_entries = max_entries
        self._entries: dict[str, _Entry] = {}

    async def get(self, key: str) -> SearchResponse | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._entries.pop(key, None)
            return None
        return entry.response

    async def put(self, key: str, response: SearchResponse) -> None:
        if len(self._entries) >= self._max_entries:
            # Cheapest correct eviction: drop the oldest insertion. dicts preserve
            # insertion order, so this is FIFO rather than LRU — good enough for a bound
            # whose only job is stopping unbounded growth in a long-lived process.
            self._entries.pop(next(iter(self._entries)), None)
        self._entries[key] = _Entry(response=response, expires_at=time.monotonic() + self._ttl_s)


class PostgresSerpCache:
    """Durable cache backed by `engine_v1.serp_cache`."""

    def __init__(self, tenant_id: UUID, *, ttl_hours: float) -> None:
        self._tenant_id = tenant_id
        self._ttl = timedelta(hours=ttl_hours)

    async def get(self, key: str) -> SearchResponse | None:
        from v1.models import SerpCacheEntry
        from v1.platform.db import session_scope

        try:
            async with session_scope(str(self._tenant_id)) as session:
                stmt = select(SerpCacheEntry).where(
                    SerpCacheEntry.tenant_id == self._tenant_id,
                    SerpCacheEntry.cache_key == key,
                    SerpCacheEntry.expires_at > datetime.now(UTC),
                )
                row = await session.scalar(stmt)
                if row is None:
                    return None
                payload = row.payload
        except Exception:
            # A cache read that fails is a cache miss, never a failed discovery run. Logged
            # with the traceback so a persistently broken cache is visible rather than
            # showing up only as a bill that stopped going down.
            logger.exception("serp_cache_read_failed", cache_key=key)
            return None

        try:
            return SearchResponse.model_validate(payload)
        except ValidationError:
            # A payload written by an older schema version. Treated as a miss and left to
            # expire rather than crashing on someone else's data.
            logger.warning("serp_cache_payload_unreadable", cache_key=key)
            return None

    async def put(self, key: str, response: SearchResponse) -> None:
        from sqlalchemy.dialects.postgresql import insert

        from v1.models import SerpCacheEntry
        from v1.platform.db import session_scope

        expires_at = datetime.now(UTC) + self._ttl
        payload = response.model_dump(mode="json")
        try:
            async with session_scope(str(self._tenant_id)) as session:
                statement = (
                    insert(SerpCacheEntry)
                    .values(
                        tenant_id=self._tenant_id,
                        cache_key=key,
                        provider=response.provider,
                        query_hash=response.query.query_hash,
                        page=response.query.page,
                        payload=payload,
                        expires_at=expires_at,
                    )
                    .on_conflict_do_update(
                        index_elements=[SerpCacheEntry.tenant_id, SerpCacheEntry.cache_key],
                        set_={"payload": payload, "expires_at": expires_at},
                    )
                )
                await session.execute(statement)
        except Exception:
            logger.exception("serp_cache_write_failed", cache_key=key)

    async def purge_expired(self) -> int:
        """Delete rows past their TTL. Not on `SerpCache` — the memory and null backends
        have nothing to purge. Nothing calls this yet; Phase 8's scheduler is what wires
        it to a cron. Until then expired rows are ignored by `get` but not reclaimed."""
        from v1.models import SerpCacheEntry
        from v1.platform.db import session_scope

        async with session_scope(str(self._tenant_id)) as session:
            result = await session.execute(
                delete(SerpCacheEntry).where(
                    SerpCacheEntry.tenant_id == self._tenant_id,
                    SerpCacheEntry.expires_at <= datetime.now(UTC),
                )
            )
        return int(result.rowcount or 0)


class NullSerpCache:
    """Never caches. Used by `/discovery/search`, the raw passthrough endpoint.

    That endpoint exists to see what a provider *actually returns right now*; serving it
    from a six-hour-old cache would make it useless for the one job it has.
    """

    async def get(self, key: str) -> SearchResponse | None:
        return None

    async def put(self, key: str, response: SearchResponse) -> None:
        return None

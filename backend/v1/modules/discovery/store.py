"""Persistence for discovery: URL novelty and pagination cursors.

Two implementations behind one protocol, and the reason is not testing convenience:

* **`InMemoryDiscoveryStore`** is what `POST /discovery/preview` uses. Preview must be able
  to render templates, issue queries and classify results *without writing a row*, because
  its whole purpose is tuning — and a tuning tool that mutates novelty state changes the
  answer the next real run gives.
* **`PostgresDiscoveryStore`** is what `POST /discovery/run` uses.

The novelty check is a set difference on `url_hash`, done in one query per batch rather
than one per URL. A discovery run sees a few hundred URLs; a round trip each would make the
novelty floor cost more than the search it is saving.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from v1.contracts.discovery import DiscoveredItem
from v1.modules.discovery.urls import host_of, url_hash
from v1.platform.logging import get_logger

logger = get_logger("v1.discovery.store")


CHAIN_SCOPE = "__chain__"
"""The cursor key used when a query runs through the failover chain.

Cursors used to be keyed on `chain[0]`, which is wrong in a way that only shows up after a
failover: the run records progress under `serper` while DataForSEO actually served the
pages. The cursor describes **how deep we have gone into this query's result space**, and
that depth is a property of the query, not of whichever provider happened to answer. So
one cursor per query, and the provider that actually served it is recorded alongside as
`last_provider` — information, not identity."""


@dataclass(slots=True)
class QueryCursor:
    """Where a template's pagination stands, and whether it is worth continuing."""

    template_id: str
    query_hash: str
    provider: str
    """Cursor scope. `CHAIN_SCOPE` for a normal run; a provider name only when a single
    provider was pinned explicitly."""

    last_provider: str | None = None
    """Who actually served the last page. Observability, never part of the key."""

    rendered_query: str = ""
    next_page: int = 1
    max_page_seen: int = 0
    novel_ratio: float | None = None
    exhausted_at: datetime | None = None
    last_run_at: datetime | None = None

    def is_exhausted(self, *, reset_after_hours: float, now: datetime | None = None) -> bool:
        """A mined-out query is skipped entirely until the reset window passes.

        Skipping is the saving. Re-running a query that returned nothing new twice in a row
        costs a credit to confirm what we already know; a weekly reset is what lets newly
        indexed pages eventually be found anyway.
        """
        if self.exhausted_at is None:
            return False
        moment = now or datetime.now(UTC)
        age_hours = (moment - self.exhausted_at).total_seconds() / 3600
        return age_hours < reset_after_hours


class DiscoveryStore(Protocol):
    async def novel_hashes(self, hashes: list[str]) -> set[str]:
        """Return the subset of `hashes` never seen before."""
        ...

    async def record_items(self, items: list[DiscoveredItem]) -> int:
        """Upsert `discovered_url` rows. Returns the number that were new."""
        ...

    async def get_cursor(
        self, *, template_id: str, query_hash: str, provider: str
    ) -> QueryCursor: ...

    async def save_cursor(self, cursor: QueryCursor) -> None: ...

    async def cursors(self) -> list[QueryCursor]: ...


class InMemoryDiscoveryStore:
    """Writes nothing durable. Used by preview, dry runs and tests."""

    def __init__(self, *, seen: set[str] | None = None) -> None:
        self._seen: set[str] = set(seen or ())
        self._cursors: dict[tuple[str, str], QueryCursor] = {}
        self.recorded: list[DiscoveredItem] = []

    async def novel_hashes(self, hashes: list[str]) -> set[str]:
        return {value for value in hashes if value not in self._seen}

    async def record_items(self, items: list[DiscoveredItem]) -> int:
        new = 0
        for item in items:
            digest = url_hash(item.url)
            if digest not in self._seen:
                self._seen.add(digest)
                new += 1
            self.recorded.append(item)
        return new

    async def get_cursor(self, *, template_id: str, query_hash: str, provider: str) -> QueryCursor:
        key = (query_hash, provider)
        if key not in self._cursors:
            self._cursors[key] = QueryCursor(
                template_id=template_id, query_hash=query_hash, provider=provider
            )
        return self._cursors[key]

    async def save_cursor(self, cursor: QueryCursor) -> None:
        self._cursors[(cursor.query_hash, cursor.provider)] = cursor

    async def cursors(self) -> list[QueryCursor]:
        return list(self._cursors.values())


class ReadThroughDiscoveryStore:
    """Reads the real state, writes nothing. What `POST /discovery/preview` uses.

    Preview started out on an empty in-memory store, which made it write nothing — correct
    — but also **read** nothing, which quietly made it lie. Every URL looked unseen, so
    novelty was always 1.0, the novelty floor never triggered, and the one screen you use
    to decide whether a template is worth keeping reported that every template was
    productive. A tuning tool that always says "yes" is worse than no tuning tool.

    So preview now reads through to Postgres for novelty and cursors, and drops every
    write. The numbers are the ones a real run would produce; the state is untouched.
    """

    def __init__(self, inner: DiscoveryStore) -> None:
        self._inner = inner
        self.skipped_writes = 0

    async def novel_hashes(self, hashes: list[str]) -> set[str]:
        return await self._inner.novel_hashes(hashes)

    async def record_items(self, items: list[DiscoveredItem]) -> int:
        self.skipped_writes += len(items)
        return 0

    async def get_cursor(self, *, template_id: str, query_hash: str, provider: str) -> QueryCursor:
        cursor = await self._inner.get_cursor(
            template_id=template_id, query_hash=query_hash, provider=provider
        )
        # Copied, not handed back by reference. `QueryCursor` is a mutable dataclass and
        # the walker advances it in place — with a store that returns its own object (the
        # in-memory one does), "writes nothing" would be quietly false: the caller would
        # mutate durable state without ever calling `save_cursor`.
        return replace(cursor)

    async def save_cursor(self, cursor: QueryCursor) -> None:
        self.skipped_writes += 1

    async def cursors(self) -> list[QueryCursor]:
        return [replace(cursor) for cursor in await self._inner.cursors()]


class PostgresDiscoveryStore:
    """Durable store backed by `engine_v1.discovered_url` and `engine_v1.search_query_state`."""

    def __init__(self, tenant_id: UUID) -> None:
        self._tenant_id = tenant_id

    async def novel_hashes(self, hashes: list[str]) -> set[str]:
        if not hashes:
            return set()
        from v1.models import DiscoveredUrl
        from v1.platform.db import session_scope

        async with session_scope(str(self._tenant_id)) as session:
            stmt = select(DiscoveredUrl.url_hash).where(
                DiscoveredUrl.tenant_id == self._tenant_id,
                DiscoveredUrl.url_hash.in_(hashes),
            )
            known = set((await session.scalars(stmt)).all())
        return {value for value in hashes if value not in known}

    async def record_items(self, items: list[DiscoveredItem]) -> int:
        if not items:
            return 0
        from v1.models import DiscoveredUrl
        from v1.platform.db import session_scope

        now = datetime.now(UTC)
        # Deduplicate within the batch first: two templates can surface the same URL in one
        # run, and Postgres refuses an ON CONFLICT statement that touches the same row
        # twice in a single command.
        by_hash: dict[str, DiscoveredItem] = {}
        for item in items:
            by_hash.setdefault(url_hash(item.url), item)

        rows = [
            {
                "tenant_id": self._tenant_id,
                "url_hash": digest,
                "url": item.url[:2048],
                "host": host_of(item.url)[:255],
                "first_seen_at": now,
                "last_seen_at": now,
                "times_seen": 1,
                "verdict": str(item.verdict),
                "verdict_reason": item.verdict_reason[:500],
                "strategy": str(item.strategy),
                "origin": item.origin,
                "title": (item.title or None),
            }
            for digest, item in by_hash.items()
        ]

        async with session_scope(str(self._tenant_id)) as session:
            statement = insert(DiscoveredUrl).values(rows)
            statement = statement.on_conflict_do_update(
                index_elements=[DiscoveredUrl.tenant_id, DiscoveredUrl.url_hash],
                set_={
                    "last_seen_at": now,
                    "times_seen": DiscoveredUrl.times_seen + 1,
                    # The verdict is refreshed because the rule table changes: a URL
                    # classified UNKNOWN last week should pick up today's rule rather than
                    # keep a verdict nobody agrees with any more.
                    "verdict": statement.excluded.verdict,
                    "verdict_reason": statement.excluded.verdict_reason,
                },
            ).returning(DiscoveredUrl.times_seen)
            result = await session.execute(statement)
            counts = [row[0] for row in result.fetchall()]

        return sum(1 for count in counts if count == 1)

    async def get_cursor(self, *, template_id: str, query_hash: str, provider: str) -> QueryCursor:
        from v1.models import SearchQueryState
        from v1.platform.db import session_scope

        async with session_scope(str(self._tenant_id)) as session:
            stmt = select(SearchQueryState).where(
                SearchQueryState.tenant_id == self._tenant_id,
                SearchQueryState.query_hash == query_hash,
                SearchQueryState.provider == provider,
            )
            row = await session.scalar(stmt)
            if row is None:
                return QueryCursor(
                    template_id=template_id, query_hash=query_hash, provider=provider
                )
            return QueryCursor(
                template_id=row.template_id,
                query_hash=row.query_hash,
                provider=row.provider,
                last_provider=row.last_provider,
                rendered_query=row.rendered_query,
                next_page=row.next_page,
                max_page_seen=row.max_page_seen,
                novel_ratio=float(row.novel_ratio) if row.novel_ratio is not None else None,
                exhausted_at=row.exhausted_at,
                last_run_at=row.last_run_at,
            )

    async def save_cursor(self, cursor: QueryCursor) -> None:
        from v1.models import SearchQueryState
        from v1.platform.db import session_scope

        values = {
            "tenant_id": self._tenant_id,
            "template_id": cursor.template_id,
            "query_hash": cursor.query_hash,
            "provider": cursor.provider,
            "last_provider": cursor.last_provider,
            "rendered_query": cursor.rendered_query[:2000],
            "next_page": cursor.next_page,
            "max_page_seen": cursor.max_page_seen,
            "novel_ratio": cursor.novel_ratio,
            "exhausted_at": cursor.exhausted_at,
            "last_run_at": cursor.last_run_at or datetime.now(UTC),
        }
        async with session_scope(str(self._tenant_id)) as session:
            statement = insert(SearchQueryState).values(**values)
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        SearchQueryState.tenant_id,
                        SearchQueryState.query_hash,
                        SearchQueryState.provider,
                    ],
                    set_={
                        key: values[key]
                        for key in (
                            "template_id",
                            "last_provider",
                            "rendered_query",
                            "next_page",
                            "max_page_seen",
                            "novel_ratio",
                            "exhausted_at",
                            "last_run_at",
                        )
                    },
                )
            )

    async def cursors(self) -> list[QueryCursor]:
        from v1.models import SearchQueryState
        from v1.platform.db import session_scope

        async with session_scope(str(self._tenant_id)) as session:
            stmt = select(SearchQueryState).where(SearchQueryState.tenant_id == self._tenant_id)
            rows = (await session.scalars(stmt)).all()

        return [
            QueryCursor(
                template_id=row.template_id,
                query_hash=row.query_hash,
                provider=row.provider,
                last_provider=row.last_provider,
                rendered_query=row.rendered_query,
                next_page=row.next_page,
                max_page_seen=row.max_page_seen,
                novel_ratio=float(row.novel_ratio) if row.novel_ratio is not None else None,
                exhausted_at=row.exhausted_at,
                last_run_at=row.last_run_at,
            )
            for row in rows
        ]

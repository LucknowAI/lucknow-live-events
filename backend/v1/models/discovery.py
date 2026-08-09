"""Discovery tables: URL novelty, pagination cursors, SERP spend, SERP cache.

All four carry `tenant_id` with an RLS policy from creation. `10 §4` is explicit that
retrofitting tenancy after the canonical model exists is far more expensive than building
the seam now, and these are the first tables in the engine that hold real volume.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from v1.models.base import Base, created_at_column, uuid_pk


class DiscoveredUrl(Base):
    """Every URL discovery has ever seen, with first/last seen and its latest verdict.

    This is the table the novelty floor runs on, which is why the unique key is the
    **hash of the normalized URL** rather than the URL itself: it is fixed-width, it
    indexes well, and hashing after normalization is what makes the normalizer's "these
    two are the same page" decision binding on every later query.
    """

    __tablename__ = "discovered_url"
    __table_args__ = (
        UniqueConstraint("tenant_id", "url_hash", name="uq_discovered_url_tenant_hash"),
        # "what did we find today", the discovery report's own query.
        Index("ix_discovered_url_tenant_first_seen", "tenant_id", "first_seen_at"),
        # "which hosts keep turning up as UNKNOWN" — how a new source gets noticed.
        Index("ix_discovered_url_tenant_host", "tenant_id", "host"),
        Index("ix_discovered_url_tenant_verdict", "tenant_id", "verdict"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )

    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    times_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    verdict_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    """Kept because "why was this URL dropped?" is otherwise unanswerable — the failure
    mode of V0's single-regex filter."""

    strategy: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(64), nullable=False)
    """Template id or source id — what we paid for this URL."""

    title: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<DiscoveredUrl {self.verdict} {self.url[:60]}>"


class SearchQueryState(Base):
    """Per template+provider pagination cursor and novelty ratio.

    The row that makes pagination stateful *across runs*. Without it, every run re-buys
    page 1 of every template — which is the cost the mentor's "next search query should
    take results from next pages" note was really about.
    """

    __tablename__ = "search_query_state"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "query_hash", "provider", name="uq_search_query_state_tenant_query"
        ),
        Index("ix_search_query_state_tenant_template", "tenant_id", "template_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )

    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rendered_query: Mapped[str] = mapped_column(String(2000), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    """Cursor scope, not the serving provider — `__chain__` for a normal run. Keying on
    the first link of the chain would record progress under a provider that failed over."""

    last_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Who actually served the last page. Reported by `GET /discovery/state`."""

    next_page: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_page_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    novel_ratio: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)
    exhausted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    """Set after two consecutive unproductive runs. An exhausted query is skipped
    entirely until the reset window passes — not paying at all beats paying less."""

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<SearchQueryState {self.template_id}@{self.provider} p{self.next_page}>"


class SearchCall(Base):
    """The SERP spend ledger — one row per query, including failures and cache hits.

    Sibling of `llm_call`. `cost_usd` is NUMERIC for the same reason: it is summed against
    a cap, and float drift on money is a bug waiting for a slow day. `cached` is on the row
    so the cache's hit rate is a measurement rather than an assumption.
    """

    __tablename__ = "search_call"
    __table_args__ = (
        # The budget breaker's query: spend for one tenant within the month.
        Index("ix_search_call_tenant_created", "tenant_id", "created_at"),
        # "which template is burning the budget".
        Index("ix_search_call_tenant_template", "tenant_id", "template_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = created_at_column()

    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter: Mapped[str] = mapped_column(String(32), nullable=False)
    query: Mapped[str] = mapped_column(String(2000), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    page: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    num: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    results_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    credits: Mapped[int | None] = mapped_column(Integer, nullable=True)

    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal(0))
    cost_is_estimated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    """Almost always true: SERP vendors bill in credits against a prepaid pack, so the
    dollar figure comes from the dated price in config rather than from the response."""

    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)

    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<SearchCall {self.provider} p{self.page} {self.outcome} ${self.cost_usd}>"


class SerpCacheEntry(Base):
    """Cached SERP responses, keyed `sha256(query|provider|page|num|gl|hl|tbs)`.

    Durable on purpose. A discovery run is a short-lived cron process, so an in-process
    cache in that shape has a 0% hit rate — and tier-A templates re-render to the same
    query on every run, which is exactly the repeat this table stops paying for.
    """

    __tablename__ = "serp_cache"
    __table_args__ = (
        UniqueConstraint("tenant_id", "cache_key", name="uq_serp_cache_tenant_key"),
        # The sweep that removes expired rows.
        Index("ix_serp_cache_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = created_at_column()

    cache_key: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<SerpCacheEntry {self.provider} p{self.page} until {self.expires_at}>"

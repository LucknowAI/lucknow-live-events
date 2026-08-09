"""discovery: discovered_url, search_query_state, search_call, serp_cache — with RLS

Revision ID: 0002_v1_discovery
Revises: 0001_v1_baseline
Create Date: 2026-08-08

Hand-written for the same reasons as the baseline: RLS policies are not something Alembic
can infer, and `10 §4` requires them to exist from each table's first day rather than being
retrofitted once the tables hold real volume.

`FORCE ROW LEVEL SECURITY` is on every table. Without it the table *owner* bypasses the
policy — and the app owning its own tables is the common single-role deployment, so the
backstop would protect nothing. It still cannot bind a superuser, which is why the app's
database role must not be one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_v1_discovery"
down_revision: str | None = "0001_v1_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "engine_v1"

# A NULL GUC (no tenant set) matches nothing — a query that forgot to set the tenant
# returns zero rows rather than every tenant's rows.
_TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('engine_v1.tenant_id', true), '')::uuid"

_TABLES = ("discovered_url", "search_query_state", "search_call", "serp_cache")


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{SCHEMA}"."{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{SCHEMA}"."{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY {table}_tenant_isolation ON "{SCHEMA}"."{table}" '
        f"USING ({_TENANT_PREDICATE}) WITH CHECK ({_TENANT_PREDICATE})"
    )


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        [f"{SCHEMA}.tenant.id"],
        name=f"fk_{table}_tenant_id_tenant",
        ondelete="CASCADE",
    )


def upgrade() -> None:
    op.create_table(
        "discovered_url",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("verdict_reason", sa.String(length=500), nullable=True),
        sa.Column("strategy", sa.String(length=16), nullable=False),
        sa.Column("origin", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=1000), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_discovered_url"),
        _tenant_fk("discovered_url"),
        sa.UniqueConstraint("tenant_id", "url_hash", name="uq_discovered_url_tenant_hash"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_discovered_url_tenant_first_seen",
        "discovered_url",
        ["tenant_id", "first_seen_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_discovered_url_tenant_host", "discovered_url", ["tenant_id", "host"], schema=SCHEMA
    )
    op.create_index(
        "ix_discovered_url_tenant_verdict",
        "discovered_url",
        ["tenant_id", "verdict"],
        schema=SCHEMA,
    )

    op.create_table(
        "search_query_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("rendered_query", sa.String(length=2000), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("last_provider", sa.String(length=64), nullable=True),
        sa.Column("next_page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_page_seen", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("novel_ratio", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("exhausted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_search_query_state"),
        _tenant_fk("search_query_state"),
        sa.UniqueConstraint(
            "tenant_id", "query_hash", "provider", name="uq_search_query_state_tenant_query"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_search_query_state_tenant_template",
        "search_query_state",
        ["tenant_id", "template_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "search_call",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("adapter", sa.String(length=32), nullable=False),
        sa.Column("query", sa.String(length=2000), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=True),
        sa.Column("page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("num", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("results_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("credits", sa.Integer(), nullable=True),
        sa.Column(
            "cost_usd", sa.Numeric(precision=12, scale=6), nullable=False, server_default="0"
        ),
        sa.Column("cost_is_estimated", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_type", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_search_call"),
        _tenant_fk("search_call"),
        schema=SCHEMA,
    )
    op.create_index("ix_search_call_created_at", "search_call", ["created_at"], schema=SCHEMA)
    op.create_index("ix_search_call_query_hash", "search_call", ["query_hash"], schema=SCHEMA)
    op.create_index(
        "ix_search_call_correlation_id", "search_call", ["correlation_id"], schema=SCHEMA
    )
    op.create_index(
        "ix_search_call_tenant_created", "search_call", ["tenant_id", "created_at"], schema=SCHEMA
    )
    op.create_index(
        "ix_search_call_tenant_template",
        "search_call",
        ["tenant_id", "template_id", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "serp_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_serp_cache"),
        _tenant_fk("serp_cache"),
        sa.UniqueConstraint("tenant_id", "cache_key", name="uq_serp_cache_tenant_key"),
        schema=SCHEMA,
    )
    op.create_index("ix_serp_cache_created_at", "serp_cache", ["created_at"], schema=SCHEMA)
    op.create_index("ix_serp_cache_expires_at", "serp_cache", ["expires_at"], schema=SCHEMA)

    for table in _TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f'DROP POLICY IF EXISTS {table}_tenant_isolation ON "{SCHEMA}"."{table}"')
    for table in reversed(_TABLES):
        op.drop_table(table, schema=SCHEMA)

"""Drop the old pipeline tables and column: raw_events, crawl_runs, events.raw_event_id.

Revision ID: 20261108120000
Revises: 20261024120000
Create Date: 2026-11-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261108120000"
down_revision = "20261024120000"
branch_labels = None
depends_on = None

# f89f806ce2ff dropped ix_crawl_runs_source_id, ix_raw_events_source_id and
# uq_raw_events_source_external_id and never recreated them, so the live schema
# has these tables with only their primary keys. DROP TABLE removes those keys,
# and Postgres removes the raw_event_id FK with its column, so no drop_index or
# drop_constraint calls are needed.


def upgrade() -> None:
    op.drop_column("events", "raw_event_id")
    op.drop_table("crawl_runs")
    op.drop_table("raw_events")


def downgrade() -> None:
    # Recreate the tables as they are at 20261024120000: with only their primary
    # keys. The three dropped indexes are not restored because they do not exist
    # at that revision either.
    op.create_table(
        "raw_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=True),
        sa.Column("raw_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ai_extracted_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extraction_method", sa.String(length=30), nullable=True),
        sa.Column(
            "extraction_confidence", sa.Float(), server_default=sa.text("0.0"), nullable=False
        ),
        sa.Column(
            "ai_flags",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("processed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "pipeline_status",
            sa.String(length=50),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name=op.f("fk_raw_events_source_id_sources")
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "crawl_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("celery_task_id", sa.String(length=200), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=True),
        sa.Column("pages_fetched", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("events_found", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("events_new", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("events_published", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("events_queued", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name=op.f("fk_crawl_runs_source_id_sources")
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("events", sa.Column("raw_event_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_events_raw_event_id_raw_events"),
        "events",
        "raw_events",
        ["raw_event_id"],
        ["id"],
    )

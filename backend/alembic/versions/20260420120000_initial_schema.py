"""initial schema: sources, raw_events, events, crawl_runs, moderation_queue, manual_submissions

Revision ID: 20260420120000
Revises:
Create Date: 2026-04-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260420120000"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("platform", sa.String(length=50), nullable=True),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("crawl_strategy", sa.String(length=50), nullable=True),
        sa.Column("config_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("crawl_interval_hours", sa.Integer(), server_default=sa.text("6"), nullable=False),
        sa.Column("trust_score", sa.Float(), server_default=sa.text("0.7"), nullable=False),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "raw_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=True),
        sa.Column("raw_payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ai_extracted_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extraction_method", sa.String(length=30), nullable=True),
        sa.Column("extraction_confidence", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("ai_flags", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("processed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("pipeline_status", sa.String(length=50), server_default=sa.text("'pending'"), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], name=op.f("fk_raw_events_source_id_sources")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_raw_events_source_id"), "raw_events", ["source_id"], unique=False)
    op.create_index(
        "uq_raw_events_source_external_id",
        "raw_events",
        ["source_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["source_id"], ["sources.id"], name=op.f("fk_crawl_runs_source_id_sources")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_crawl_runs_source_id"), "crawl_runs", ["source_id"], unique=False)

    op.create_table(
        "moderation_queue",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=True),
        sa.Column("status", sa.String(length=30), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("ai_verdict", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "manual_submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submitter_name", sa.String(length=200), nullable=True),
        sa.Column("submitter_email", sa.String(length=300), nullable=True),
        sa.Column("event_url", sa.Text(), nullable=True),
        sa.Column("poster_key", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=30), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slug", sa.String(length=300), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("short_description", sa.String(length=500), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(length=100), server_default=sa.text("'Asia/Kolkata'"), nullable=False),
        sa.Column("city", sa.String(length=100), server_default=sa.text("'Lucknow'"), nullable=False),
        sa.Column("locality", sa.String(length=200), nullable=True),
        sa.Column("venue_name", sa.String(length=500), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=True),
        sa.Column("topics_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("audience_json", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("organizer_name", sa.String(length=300), nullable=True),
        sa.Column("community_name", sa.String(length=300), nullable=True),
        sa.Column("source_platform", sa.String(length=50), nullable=True),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("registration_url", sa.Text(), nullable=True),
        sa.Column("poster_url", sa.Text(), nullable=True),
        sa.Column("banner_color", sa.String(length=7), nullable=True),
        sa.Column("price_type", sa.String(length=20), nullable=True),
        sa.Column("is_free", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_featured", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_cancelled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_student_friendly", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("relevance_score", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("publish_score", sa.Float(), server_default=sa.text("0.0"), nullable=False),
        sa.Column("raw_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["raw_event_id"], ["raw_events.id"], name=op.f("fk_events_raw_event_id_raw_events")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_events_published_at"), "events", ["published_at"], unique=False)
    op.create_index(op.f("ix_events_slug"), "events", ["slug"], unique=True)
    op.create_index(op.f("ix_events_start_at"), "events", ["start_at"], unique=False)
    op.create_index("idx_events_search", "events", ["search_vector"], unique=False, postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("idx_events_search", table_name="events")
    op.drop_index(op.f("ix_events_start_at"), table_name="events")
    op.drop_index(op.f("ix_events_slug"), table_name="events")
    op.drop_index(op.f("ix_events_published_at"), table_name="events")
    op.drop_table("events")

    op.drop_table("manual_submissions")
    op.drop_table("moderation_queue")

    op.drop_index(op.f("ix_crawl_runs_source_id"), table_name="crawl_runs")
    op.drop_table("crawl_runs")

    op.drop_index("uq_raw_events_source_external_id", table_name="raw_events")
    op.drop_index(op.f("ix_raw_events_source_id"), table_name="raw_events")
    op.drop_table("raw_events")

    op.drop_table("sources")

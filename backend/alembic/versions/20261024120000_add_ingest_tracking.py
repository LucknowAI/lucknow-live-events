"""Add ingest tracking: event identity/hash columns, ingest_runs, event_changes.

Revision ID: 20261024120000
Revises: 5568dd7d9489
Create Date: 2026-10-24
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20261024120000"
down_revision = "5568dd7d9489"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("fetched", sa.Integer(), server_default="0", nullable=False),
        sa.Column("new", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rejected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reject_reasons", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name="ck_ingest_runs_status"
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_ingest_runs_source_id_sources",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ingest_runs_source_id_started_at", "ingest_runs", ["source_id", "started_at"]
    )

    op.create_table(
        "event_changes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("diff", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["event_id"],
            ["events.id"],
            name="fk_event_changes_event_id_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["ingest_runs.id"],
            name="fk_event_changes_run_id_ingest_runs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_changes_event_id", "event_changes", ["event_id"])

    op.add_column("events", sa.Column("source_id", sa.UUID(), nullable=True))
    op.add_column("events", sa.Column("external_id", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("content_hash", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_events_source_id_sources",
        "events",
        "sources",
        ["source_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_events_source_id", "events", ["source_id"])
    op.create_unique_constraint(
        "uq_events_source_platform_external_id", "events", ["source_platform", "external_id"]
    )

    # Link existing events to their source while raw_events still exists (dropped in a later
    # migration). Events without a raw_event keep source_id NULL.
    op.execute(
        """
        UPDATE events e
        SET source_id = r.source_id
        FROM raw_events r
        WHERE r.id = e.raw_event_id
        """
    )


def downgrade() -> None:
    op.drop_constraint("uq_events_source_platform_external_id", "events", type_="unique")
    op.drop_index("ix_events_source_id", table_name="events")
    op.drop_constraint("fk_events_source_id_sources", "events", type_="foreignkey")
    op.drop_column("events", "last_seen_at")
    op.drop_column("events", "content_hash")
    op.drop_column("events", "external_id")
    op.drop_column("events", "source_id")

    op.drop_index("ix_event_changes_event_id", table_name="event_changes")
    op.drop_table("event_changes")
    op.drop_index("ix_ingest_runs_source_id_started_at", table_name="ingest_runs")
    op.drop_table("ingest_runs")

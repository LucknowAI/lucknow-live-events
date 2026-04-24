"""Populate events.search_vector via trigger + backfill.

Revision ID: 20260421120000
Revises: 20260420120000
Create Date: 2026-04-21
"""

from __future__ import annotations

from alembic import op

revision = "20260421120000"
down_revision = "20260420120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION events_search_vector_update() RETURNS trigger AS $$
        BEGIN
          NEW.search_vector := to_tsvector('english',
            coalesce(NEW.title, '') || ' ' ||
            coalesce(NEW.short_description, '') || ' ' ||
            coalesce(NEW.community_name, '') || ' ' ||
            coalesce(NEW.organizer_name, '') || ' ' ||
            coalesce(NEW.locality, '')
          );
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_events_search_vector ON events;")
    op.execute(
        """
        CREATE TRIGGER trg_events_search_vector
        BEFORE INSERT OR UPDATE OF title, short_description, community_name, organizer_name, locality
        ON events
        FOR EACH ROW
        EXECUTE PROCEDURE events_search_vector_update();
        """
    )
    op.execute(
        """
        UPDATE events SET title = title;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_events_search_vector ON events;")
    op.execute("DROP FUNCTION IF EXISTS events_search_vector_update();")

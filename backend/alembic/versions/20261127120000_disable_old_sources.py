"""Disable sources without an adapter and drop Unstop competitions.

Data only. Rows are disabled, never deleted: their events keep source_id, and
cleanup still hides them. Downgrade is a no-op: the old enabled flags and
config are not kept.

Revision ID: 20261127120000
Revises: 20261108120000
Create Date: 2026-11-27
"""

from __future__ import annotations

from alembic import op

revision = "20261127120000"
down_revision = "20261108120000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE sources SET enabled = false "
        "WHERE enabled AND platform NOT IN ('bevy', 'luma', 'unstop')"
    )
    op.execute(
        "UPDATE sources SET config_json = jsonb_set(config_json, '{opportunity_types}', "
        "(config_json -> 'opportunity_types') - 'competitions') "
        "WHERE platform = 'unstop' AND config_json -> 'opportunity_types' ? 'competitions'"
    )


def downgrade() -> None:
    pass

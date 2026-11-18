from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import DateTime

from api.models.base import Base, uuid_pk


class IngestRun(Base):
    """One engine run for one source: what was fetched and what happened to it."""

    __tablename__ = "ingest_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running', 'success', 'failed')", name="ck_ingest_runs_status"),
        Index("ix_ingest_runs_source_id_started_at", "source_id", "started_at"),
    )

    id: Mapped[str] = uuid_pk()
    source_id: Mapped[str] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")

    fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    new: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unchanged: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # reason -> count, e.g. {"outside_radius": 3, "online_not_allowlisted": 1}
    reject_reasons: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    error: Mapped[str | None] = mapped_column(Text)


class EventChange(Base):
    """Audit row written whenever a run changes a stored event."""

    __tablename__ = "event_changes"

    id: Mapped[str] = uuid_pk()
    event_id: Mapped[str] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Kept when the run is deleted with its source, so event history survives.
    run_id: Mapped[str | None] = mapped_column(ForeignKey("ingest_runs.id", ondelete="SET NULL"))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # {field: [old, new]}
    diff: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

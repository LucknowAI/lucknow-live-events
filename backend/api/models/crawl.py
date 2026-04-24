from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import DateTime

from api.models.base import Base, uuid_pk


class CrawlRun(Base):
    __tablename__ = "crawl_runs"

    id: Mapped[str] = uuid_pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(30))
    pages_fetched: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    events_found: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    events_new: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    events_published: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    events_queued: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    source = relationship("Source", back_populates="crawl_runs")


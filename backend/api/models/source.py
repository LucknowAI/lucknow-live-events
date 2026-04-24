from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from api.models.base import Base, uuid_pk


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    platform: Mapped[str | None] = mapped_column(String(50))
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    # status: active (default), whitelisted (always crawled, high trust), blacklisted (never crawled)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")
    crawl_strategy: Mapped[str | None] = mapped_column(String(50))
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    crawl_interval_hours: Mapped[int] = mapped_column(Integer, nullable=False, server_default="6")
    trust_score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.7")

    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    raw_events = relationship("RawEvent", back_populates="source")
    crawl_runs = relationship("CrawlRun", back_populates="source")



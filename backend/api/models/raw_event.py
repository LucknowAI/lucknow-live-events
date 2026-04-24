from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import DateTime

from api.models.base import Base, uuid_pk


class RawEvent(Base):
    __tablename__ = "raw_events"

    id: Mapped[str] = uuid_pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(500))

    raw_payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ai_extracted_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_method: Mapped[str | None] = mapped_column(String(30))
    extraction_confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")
    ai_flags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")

    seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    pipeline_status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="pending")

    source = relationship("Source", back_populates="raw_events")
    event = relationship("Event", back_populates="raw_event", uselist=False)


from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.types import DateTime

from api.models.base import Base, uuid_pk


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = uuid_pk()
    slug: Mapped[str] = mapped_column(String(300), nullable=False, unique=True, index=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    short_description: Mapped[str | None] = mapped_column(String(500))

    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, server_default="Asia/Kolkata")
    city: Mapped[str] = mapped_column(String(100), nullable=False, server_default="Lucknow")
    locality: Mapped[str | None] = mapped_column(String(200))
    venue_name: Mapped[str | None] = mapped_column(String(500))
    address: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)

    mode: Mapped[str | None] = mapped_column(String(20))
    event_type: Mapped[str | None] = mapped_column(String(50))
    topics_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    audience_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")

    organizer_name: Mapped[str | None] = mapped_column(String(300))
    community_name: Mapped[str | None] = mapped_column(String(300))
    source_platform: Mapped[str | None] = mapped_column(String(50))

    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    registration_url: Mapped[str | None] = mapped_column(Text)
    poster_url: Mapped[str | None] = mapped_column(Text)
    banner_color: Mapped[str | None] = mapped_column(String(7))

    price_type: Mapped[str | None] = mapped_column(String(20))
    is_free: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    is_featured: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    is_student_friendly: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    date_tba: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")
    publish_score: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.0")

    raw_event_id: Mapped[str | None] = mapped_column(ForeignKey("raw_events.id"))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    search_vector: Mapped[Any | None] = mapped_column(TSVECTOR)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    raw_event = relationship("RawEvent", back_populates="event")


Index("idx_events_search", Event.search_vector, postgresql_using="gin")


from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EventBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    slug: str
    title: str
    description: str | None = None
    short_description: str | None = None

    start_at: datetime
    end_at: datetime | None = None
    timezone: str = "Asia/Kolkata"
    city: str = "Lucknow"
    locality: str | None = None
    venue: str | None = Field(default=None, validation_alias="venue_name")
    address: str | None = None
    lat: float | None = None
    lng: float | None = None

    mode: str | None = None
    event_type: str | None = None
    topics: list[Any] = Field(default_factory=list, validation_alias="topics_json")
    audience: list[Any] = Field(default_factory=list, validation_alias="audience_json")

    organizer_name: str | None = None
    community_name: str | None = None
    source_platform: str | None = None

    canonical_url: str
    registration_url: str | None = None
    poster_url: str | None = None
    banner_color: str | None = None

    price_type: str | None = None
    is_free: bool = True
    is_featured: bool = False
    is_cancelled: bool = False
    is_student_friendly: bool = False
    date_tba: bool = False

    relevance_score: float = 0.0
    publish_score: float = 0.0

    published_at: datetime | None = None
    expires_at: datetime | None = None
    updated_at: datetime
    created_at: datetime


class EventListResponse(BaseModel):
    items: list[EventBase]
    page: int
    limit: int
    total: int


class EventDetailResponse(EventBase):
    pass

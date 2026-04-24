from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from sqlalchemy.types import DateTime

from api.models.base import Base, uuid_pk


class ManualSubmission(Base):
    __tablename__ = "manual_submissions"

    id: Mapped[str] = uuid_pk()
    # "event" for event URL submissions, "community" for community link submissions
    submission_type: Mapped[str] = mapped_column(String(30), nullable=False, server_default="event")
    submitter_name: Mapped[str | None] = mapped_column(String(200))
    submitter_email: Mapped[str | None] = mapped_column(String(300))
    # For event submissions
    event_url: Mapped[str | None] = mapped_column(Text)
    poster_key: Mapped[str | None] = mapped_column(Text)
    # For community submissions
    community_name: Mapped[str | None] = mapped_column(String(300))
    community_url: Mapped[str | None] = mapped_column(Text)
    community_description: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

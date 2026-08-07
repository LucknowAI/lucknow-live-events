"""Event contracts.

Phase 1 needs exactly one real event contract: the schema an extraction call asks a
provider to fill. It is deliberately minimal — `ExtractedEvent` is what a *single
source sighting* claims, before normalization (Phase 5) and before it becomes a
canonical event (Phase 7). Fields are added when a phase actually consumes them.

Two contract rules are enforced here rather than left to callers:

1. **The TBA contract.** `date_tba` / `venue_tba` are derived, never assigned. They are
   computed properties, so no caller and no model can set them inconsistently with the
   fields they describe. (The parent plan said "derived in a model_validator"; a
   computed property is the stronger form — it is also absent from the validation JSON
   Schema, so a provider is never asked to fill a field it has no business filling.)
2. **Date sanity as a signal, not a rejection.** An implausible date does not raise —
   raising would throw away a real sighting. `date_sanity` reports it so the extraction
   stage can quarantine the observation. A sentinel date is never substituted.

No schema reachable through the LLM port may set `extra="forbid"`: Pydantic then emits
`additionalProperties`, which `google-genai`'s client-side transformer rejects
(googleapis/python-genai#1815). Strictness is applied by our own validation instead.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any

from dateutil import parser as date_parser
from pydantic import BaseModel, BeforeValidator, Field, computed_field

# Sanity window for a claimed event date. Outside this, the claim is almost certainly a
# parse artefact (a copyright year, a "since 1998" line, a placeholder).
PAST_WINDOW = timedelta(days=365)
FUTURE_WINDOW = timedelta(days=730)


class DateSanity(StrEnum):
    OK = "ok"
    MISSING = "missing"
    TOO_OLD = "too_old"
    TOO_FUTURE = "too_future"


class AttendanceMode(StrEnum):
    IN_PERSON = "in_person"
    ONLINE = "online"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


def _coerce_datetime(value: Any) -> Any:
    """Accept anything date-like a model might emit; leave the rest for Pydantic.

    Models are asked for ISO-8601 and mostly comply, but "March 14, 2026 6:00 PM" and
    "2026-03-14T18:00+05:30" both turn up in practice. Parsing them here means a
    cosmetic format difference does not burn a repair attempt. Anything genuinely
    unparseable falls through and fails validation, which is the intended behaviour.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text.lower() in {"n/a", "na", "none", "null", "tba", "tbd", "unknown"}:
        return None
    try:
        return date_parser.parse(text)
    except (ValueError, OverflowError, date_parser.ParserError):
        return value


LenientDatetime = Annotated[datetime | None, BeforeValidator(_coerce_datetime)]


class ExtractedEvent(BaseModel):
    """One source's claim about one event, as extracted from one capture."""

    title: str = Field(min_length=1, max_length=500, description="Event title as published.")
    description: str | None = Field(
        default=None, max_length=8000, description="Short summary of the event."
    )
    start_at: LenientDatetime = Field(
        default=None, description="Start date/time in ISO-8601. Null if not stated."
    )
    end_at: LenientDatetime = Field(
        default=None, description="End date/time in ISO-8601. Null if not stated."
    )
    timezone_name: str | None = Field(
        default=None, max_length=64, description="IANA timezone if the page states one."
    )
    venue_name: str | None = Field(default=None, max_length=300)
    address: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=120)
    organizer_name: str | None = Field(default=None, max_length=300)
    attendance_mode: AttendanceMode = AttendanceMode.UNKNOWN
    is_free: bool | None = Field(default=None, description="Null when the page is silent.")
    price_text: str | None = Field(default=None, max_length=200)
    registration_url: str | None = Field(default=None, max_length=2000)
    topics: list[str] = Field(default_factory=list, max_length=20)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def date_tba(self) -> bool:
        """No usable start time was claimed."""
        return self.start_at is None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def venue_tba(self) -> bool:
        """No usable location was claimed, and it is not an online event."""
        if self.attendance_mode is AttendanceMode.ONLINE:
            return False
        return not (self.venue_name or self.address)

    def date_sanity(self, *, now: datetime | None = None) -> DateSanity:
        """Whether `start_at` is plausible. Reported, not enforced — see module docstring."""
        if self.start_at is None:
            return DateSanity.MISSING
        reference = now or datetime.now(UTC)
        start = self.start_at
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        if start < reference - PAST_WINDOW:
            return DateSanity.TOO_OLD
        if start > reference + FUTURE_WINDOW:
            return DateSanity.TOO_FUTURE
        return DateSanity.OK

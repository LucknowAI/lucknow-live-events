"""The one event shape every adapter returns.

`store.py` (#11) copies these fields onto `events` columns. The content hash
covers this model only: no `last_seen_at`, `source_id`, or the city/timezone
defaults the store fills in. Dates are UTC so the same instant always hashes
the same. `source_platform` is the adapter key (`bevy`, `unstop`, `luma`).
The runner (#16) must check it matches the registry key.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _web_url(value: str) -> str:
    # Platform data is user-written (anyone can create a Luma event), and these
    # are rendered as links, so `javascript:` and friends must not get through.
    if not value.lower().startswith(("https://", "http://")):
        raise ValueError("must be an http(s) URL")
    return value


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_as_utc)]
WebUrl = Annotated[str, AfterValidator(_web_url)]


class NormalizedEvent(BaseModel):
    # Stripped so stray whitespace from a platform never reads as a content change.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str | None = None
    start_at: UtcDatetime
    end_at: UtcDatetime | None = None
    mode: Literal["online", "offline"]
    venue_name: str | None = None
    address: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    organizer_name: str | None = None
    canonical_url: WebUrl
    registration_url: WebUrl | None = None
    poster_url: WebUrl | None = None
    # None when the platform doesn't say. The column is NOT NULL; store.py applies its default.
    is_free: bool | None = None
    source_platform: str

    @model_validator(mode="after")
    def _end_not_before_start(self) -> "NormalizedEvent":
        if self.end_at is not None and self.end_at < self.start_at:
            raise ValueError("end_at is before start_at")
        return self

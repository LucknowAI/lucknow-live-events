from __future__ import annotations

from datetime import datetime, timezone

from dateutil import parser as dateutil_parser
import pytz

_IST = pytz.timezone("Asia/Kolkata")


def parse_datetime(value: str | None) -> datetime | None:
    """Parse a date/time string and return a timezone-aware datetime.

    Assumes Asia/Kolkata if no timezone info is present.
    """
    if not value:
        return None
    try:
        dt = dateutil_parser.parse(value)
        if dt.tzinfo is None:
            dt = _IST.localize(dt)
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None

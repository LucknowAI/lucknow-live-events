from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from engine.models import NormalizedEvent

EVENT = {
    "external_id": "evt-1",
    "title": "GDG Lucknow meetup",
    "start_at": datetime(2026, 10, 25, 10, 0, tzinfo=UTC),
    "mode": "offline",
    "canonical_url": "https://gdg.community.dev/events/evt-1",
    "source_platform": "bevy",
}


def test_rejects_naive_datetime():
    with pytest.raises(ValidationError):
        NormalizedEvent(**{**EVENT, "start_at": datetime(2026, 10, 25, 10, 0)})


def test_rejects_non_web_url():
    with pytest.raises(ValidationError):
        NormalizedEvent(**{**EVENT, "registration_url": "javascript:alert(1)"})

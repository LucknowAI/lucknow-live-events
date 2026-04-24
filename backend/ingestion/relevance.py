from __future__ import annotations

from dataclasses import dataclass

from ingestion.location_data import (
    LUCKNOW_COMMUNITIES,
    LUCKNOW_INSTITUTIONS,
    LUCKNOW_LOCALITIES,
)


@dataclass
class NormalizedEventData:
    mode: str | None
    city: str | None
    address: str | None
    venue_name: str | None
    organizer_name: str | None
    community_name: str | None


def compute_relevance(event: NormalizedEventData) -> float:
    """Return 0.0–1.0 Lucknow relevance score.

    Logic mirrors requirements §11 exactly.
    """
    mode = (event.mode or "").lower()

    if mode == "offline" or mode == "":
        text = " ".join(
            filter(None, [event.city, event.address, event.venue_name])
        ).lower()
        if "lucknow" in text:
            return 1.0
        if any(loc in text for loc in LUCKNOW_LOCALITIES):
            return 0.95
        if any(inst in text for inst in LUCKNOW_INSTITUTIONS):
            return 0.90
        return 0.1  # offline event outside Lucknow

    if mode in ("online", "hybrid"):
        org_text = " ".join(
            filter(None, [event.organizer_name, event.community_name])
        ).lower()
        if any(c in org_text for c in LUCKNOW_COMMUNITIES):
            return 0.90
        if "lucknow" in org_text:
            return 0.85
        return 0.50  # generic online event — include but lower priority

    return 0.3

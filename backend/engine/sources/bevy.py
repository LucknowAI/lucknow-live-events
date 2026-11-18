"""GDG chapters on Bevy (gdg.community.dev), read from its public JSON API.

Source config: `{"chapter_id": 1227}`. The chapter id is in each chapter page's
source (`chapter_id = '1227'`). Bevy's search ignores the city and uses the
caller's location, so every chapter is its own `sources` row.
"""

from typing import Any

from engine import http
from engine.models import NormalizedEvent
from engine.sources.base import Rejection
from engine.text import html_to_text

API = "https://gdg.community.dev/api"
# `Live` = upcoming and ongoing only; `Published` also returns every past event.
# Same filter the chapter page's own "upcoming events" widget uses.
_LIST_PARAMS = {"status": "Live", "include_cohosted_events": "true", "page_size": 50}
# A chapter has a handful of live events; more pages than this means the API is looping.
_MAX_PAGES = 10


def _known(value: str | None) -> str | None:
    """Bevy fills unannounced venue fields with "TBA"."""
    if value is None or value.strip().upper() in ("", "TBA"):
        return None
    return value


def _address(raw: dict[str, Any]) -> str | None:
    # The city goes in the address: NormalizedEvent has no city field, and the
    # Lucknow check falls back to the address when there are no coordinates.
    address, city, zip_code = (
        _known(raw.get(key)) for key in ("venue_address", "venue_city", "venue_zip_code")
    )
    if address and city and city.lower() in address.lower():
        city = None
    return ", ".join(part for part in (address, city, zip_code) if part) or None


def _is_free(tickets: list[dict[str, Any]]) -> bool | None:
    if not tickets:
        return None
    return all(not ticket.get("price") for ticket in tickets)


class Bevy:
    async def list_events(self, config: dict) -> list[dict]:
        url: str | None = f"{API}/event_slim/for_chapter/{int(config['chapter_id'])}/"
        params: dict | None = _LIST_PARAMS
        items: list[dict] = []
        for _ in range(_MAX_PAGES):
            page = (await http.get(url, params=params)).json()
            items.extend(page["results"])
            url = page["links"]["next"]
            if url is None:
                return items
            params = None  # The next link already carries the query.
        raise RuntimeError(f"Bevy chapter {config['chapter_id']}: more than {_MAX_PAGES} pages")

    def external_id(self, item: dict) -> str:
        return str(item["id"])

    async def fetch_detail(self, item: dict) -> dict:
        # The listing has no venue, tickets or full description.
        return (await http.get(f"{API}/event/{int(item['id'])}/")).json()

    def to_event(self, raw: dict, config: dict) -> NormalizedEvent | Rejection:
        if raw.get("is_test"):
            return Rejection("test_event")
        return NormalizedEvent(
            external_id=str(raw["id"]),
            title=raw["title"],
            description=html_to_text(raw.get("description")),
            start_at=raw["start_date"],
            end_at=raw.get("end_date"),
            # Not `is_virtual_event`: it is inverted on real events. `audience_type`
            # is VIRTUAL, IN_PERSON or HYBRID; hybrid has a venue, so it counts as offline.
            mode="online" if raw.get("audience_type") == "VIRTUAL" else "offline",
            venue_name=_known(raw.get("venue_name")),
            address=_address(raw),
            lat=raw.get("venue_latitude"),
            lng=raw.get("venue_longitude"),
            organizer_name=(raw.get("chapter") or {}).get("title"),
            canonical_url=raw["url"],
            registration_url=raw.get("custom_tickets_url") or None,
            poster_url=(raw.get("banner") or {}).get("url") or None,
            is_free=_is_free(raw.get("tickets") or []),
            source_platform="bevy",
        )

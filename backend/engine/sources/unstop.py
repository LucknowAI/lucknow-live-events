"""Unstop's public opportunity API; config lists opportunity_types to search.

Live listings omit start_date, so fetch_detail fills it from the public detail
endpoint. Registration dates are never used as event dates.
"""

from engine import http
from engine.models import NormalizedEvent
from engine.sources.base import Rejection
from engine.text import html_to_text

API = "https://unstop.com/api/public"
_TYPES = {"hackathons", "competitions", "workshops"}
_MAX_PAGES = 100


class Unstop:
    async def list_events(self, config: dict) -> list[dict]:
        kinds = config["opportunity_types"]
        if (
            not isinstance(kinds, list)
            or not kinds
            or any(not isinstance(k, str) or k not in _TYPES for k in kinds)
        ):
            raise ValueError(
                "Unstop opportunity_types must list hackathons, competitions or workshops"
            )
        items: dict[str, dict] = {}
        for kind in dict.fromkeys(kinds):
            for number in range(1, _MAX_PAGES + 1):
                params = {
                    "opportunity": kind,
                    "searchTerm": "lucknow",
                    "per_page": 50,
                    "page": number,
                }
                response = await http.get(f"{API}/opportunity/search-result", params=params)
                page = response.json()["data"]
                if page["current_page"] != number:
                    raise RuntimeError("Unstop pagination did not advance")
                for item in page["data"]:
                    items.setdefault(self.external_id(item), item)
                if page["next_page_url"] is None:
                    break
            else:
                raise RuntimeError(f"Unstop {kind}: more than {_MAX_PAGES} pages")
        return list(items.values())

    def external_id(self, item: dict) -> str:
        return str(item["id"])

    async def fetch_detail(self, item: dict) -> dict:
        if item.get("start_date"):
            return item
        response = await http.get(f"{API}/competition/{int(item['id'])}")
        detail = response.json()["data"]["competition"]
        if self.external_id(detail) != self.external_id(item):
            raise ValueError("Unstop detail ID differs from listing ID")
        return {**item, **detail}

    def to_event(self, raw: dict, config: dict) -> NormalizedEvent | Rejection:
        if raw.get("region") not in ("online", "offline", "hybrid"):
            return Rejection("unsupported_region")
        if not raw.get("start_date"):
            return Rejection("missing_event_start")
        place = raw.get("address_with_country_logo") or {}
        if not any(
            place.get(k) not in (None, "")
            for k in ("address", "city", "name", "state", "lat", "lng")
        ):
            place = (raw.get("locations") or [place])[0]
        if isinstance(place, str):
            place = {"address": place}
        parts = [place.get("address"), place.get("city") or place.get("name"), place.get("state")]
        address = ", ".join(dict.fromkeys(part for part in parts if part)) or None
        url = "https://unstop.com/" + raw["public_url"].lstrip("/")
        paid = raw.get("paid", raw.get("isPaid"))
        return NormalizedEvent(
            external_id=self.external_id(raw),
            title=raw["title"],
            description=html_to_text(raw.get("details")),
            start_at=raw["start_date"],
            end_at=raw.get("end_date"),
            mode="online" if raw["region"] == "online" else "offline",
            venue_name=place.get("address"),
            address=address,
            lat=place.get("lat"),
            lng=place.get("lng"),
            organizer_name=(raw.get("organisation") or {}).get("name"),
            canonical_url=url,
            registration_url=raw.get("regn_url") or url,
            poster_url=raw.get("logoUrl2") or None,
            is_free=not paid if isinstance(paid, (bool, int)) and paid in (0, 1) else None,
            source_platform="unstop",
        )

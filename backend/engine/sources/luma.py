"""Public Luma discovery; config uses category names and stable host api_ids."""

from engine import http
from engine.models import NormalizedEvent
from engine.sources.base import Rejection

API = "https://api.lu.ma"
_MAX_PAGES = 100


def _text(node: dict) -> str:
    if node.get("type") == "text":
        return node["text"]
    text = "".join(_text(child) for child in node.get("content", []))
    return text + (
        "\n" if node.get("type") in ("paragraph", "heading", "hard_break", "hardBreak") else ""
    )


class Luma:
    async def list_events(self, config: dict) -> list[dict]:
        params = {
            "latitude": config["latitude"],
            "longitude": config["longitude"],
            "pagination_limit": 50,
        }
        items, cursors = {}, set()
        for _ in range(_MAX_PAGES):
            page = (await http.get(f"{API}/discover/get-paginated-events", params=params)).json()
            for entry in page["entries"]:
                event = entry["event"]
                items.setdefault(self.external_id(event), event)
            if not page["has_more"]:
                return list(items.values())
            cursor = page.get("next_cursor")
            if not cursor or cursor in cursors:
                raise RuntimeError("Luma pagination did not advance")
            cursors.add(cursor)
            params = {**params, "pagination_cursor": cursor}
        raise RuntimeError(f"Luma: more than {_MAX_PAGES} pages")

    def external_id(self, item: dict) -> str:
        return item["api_id"]

    async def fetch_detail(self, item: dict) -> dict:
        raw = (
            await http.get(f"{API}/event/get", params={"event_api_id": self.external_id(item)})
        ).json()
        if self.external_id(raw["event"]) != self.external_id(item):
            raise ValueError("Luma detail ID differs from listing ID")
        return raw

    def to_event(self, raw: dict, config: dict) -> NormalizedEvent | Rejection:
        hosts = raw.get("hosts") or []
        categories = {category["name"] for category in raw.get("categories") or []}
        host_ids = {host["api_id"] for host in hosts}
        organizer_names = [host["name"] for host in hosts if host.get("name")]
        if not (
            categories.intersection(config.get("categories", []))
            or host_ids.intersection(config.get("allowed_hosts", []))
        ):
            return Rejection("category_not_allowed")
        event = raw["event"]
        location_type = event.get("location_type")
        if location_type not in ("online", "offline", "hybrid"):
            return Rejection("unsupported_location_type")
        place, coordinate = event.get("geo_address_info") or {}, event.get("coordinate") or {}
        url = "https://luma.com/" + event["url"].lstrip("/")
        return NormalizedEvent(
            external_id=self.external_id(event),
            title=event["name"],
            description=_text(raw.get("description_mirror") or {}).strip() or None,
            start_at=event["start_at"],
            end_at=event.get("end_at"),
            mode="offline" if location_type == "hybrid" else location_type,
            venue_name=place.get("address"),
            address=place.get("full_address") or place.get("city_state") or place.get("city"),
            lat=coordinate.get("latitude"),
            lng=coordinate.get("longitude"),
            organizer_name=", ".join(organizer_names[:3]) or None,
            canonical_url=url,
            registration_url=url,
            poster_url=event.get("cover_url") or None,
            is_free=(raw.get("ticket_info") or {}).get("is_free"),
            source_platform="luma",
        )

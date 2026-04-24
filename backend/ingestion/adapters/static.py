from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ingestion.adapters.base import BaseAdapter, ScrapedPage


class StaticAdapter(BaseAdapter):
    """
    Dev/testing adapter.

    It reads fully-structured event dicts from `source.config_json.events` and returns them as
    a single JSON page. This lets us test the end-to-end ingestion → API → UI flow without
    depending on external websites or AI quota.
    """

    platform = "static"
    crawl_strategy = "static"

    async def fetch(self, source: dict[str, Any]) -> list[ScrapedPage]:
        cfg = source.get("config_json") or {}
        events = cfg.get("events") or []
        if not isinstance(events, list):
            raise ValueError("static source config_json.events must be a list")

        return [
            ScrapedPage(
                url=source.get("base_url") or "static://events",
                html_or_json={"results": events},
                fetched_at=datetime.now(timezone.utc),
                status_code=200,
                page_type="api_response",
            )
        ]

    def extract_raw_events(self, page: ScrapedPage) -> list[dict[str, Any]]:
        data = page.html_or_json
        if isinstance(data, dict):
            items = data.get("results") or data.get("events") or []
            return [x for x in items if isinstance(x, dict)]
        return []

    def get_external_id(self, raw: dict[str, Any]) -> str | None:
        v = raw.get("_id") or raw.get("id")
        return str(v) if v is not None else None


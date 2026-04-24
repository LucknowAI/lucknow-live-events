from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal


PageType = Literal["listing", "detail", "api_response"]


@dataclass(slots=True)
class ScrapedPage:
    url: str
    html_or_json: str | dict[str, Any]
    fetched_at: datetime
    status_code: int
    page_type: PageType


class BaseAdapter(ABC):
    platform: str
    crawl_strategy: str

    @abstractmethod
    async def fetch(self, source: dict[str, Any]) -> list[ScrapedPage]:
        ...

    @abstractmethod
    def extract_raw_events(self, page: ScrapedPage) -> list[dict[str, Any]]:
        ...

    def get_external_id(self, raw: dict[str, Any]) -> str | None:
        return None


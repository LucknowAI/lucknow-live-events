from typing import NamedTuple, Protocol

from engine.models import NormalizedEvent


class Rejection(NamedTuple):
    reason: str


class Source(Protocol):
    """Adapter contract. No database writes. Network calls go through `engine.http` only.

    `config` is that source row's `config_json`, for example `{"chapter_id": 1227}`.
    Register an instance: `REGISTRY["bevy"] = Bevy()`.

    `list_events` and `fetch_detail` are async; the runner awaits them one at a time.
    `external_id` comes from the listing item, before `fetch_detail`.
    `to_event` returns `Rejection(reason)` when this platform's config excludes
    the row. The reason is recorded on the run.
    """

    async def list_events(self, config: dict) -> list[dict]: ...

    def external_id(self, item: dict) -> str: ...

    async def fetch_detail(self, item: dict) -> dict: ...

    def to_event(self, raw: dict, config: dict) -> NormalizedEvent | Rejection: ...

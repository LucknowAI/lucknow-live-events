"""Change detection for engine events. Pure functions: no DB, no network."""

import hashlib
import json
from typing import Any, Literal

from engine.models import NormalizedEvent


def content_hash(event: NormalizedEvent) -> str:
    # Sorted keys give a fixed field order, so the same content always hashes the same.
    payload = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def classify(
    event: NormalizedEvent, stored_hash: str | None
) -> Literal["new", "changed", "unchanged"]:
    """`stored_hash` is None when the event is not stored yet."""
    if stored_hash is None:
        return "new"
    return "unchanged" if content_hash(event) == stored_hash else "changed"


def diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, list[Any]]:
    return {
        field: [old.get(field), value] for field, value in new.items() if old.get(field) != value
    }

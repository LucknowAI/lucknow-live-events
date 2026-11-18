import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

from engine.models import NormalizedEvent
from engine.sources import REGISTRY
from engine.sources import bevy as bevy_module

FIXTURES = Path(__file__).parents[1] / "fixtures" / "bevy"
# Real responses (trimmed to the fields the adapter reads), keyed by the URL that returned them.
RESPONSES = {
    f"{bevy_module.API}/event_slim/for_chapter/1227/": "for_chapter_1227_live.json",
    f"{bevy_module.API}/event/133425/": "event_133425.json",
    f"{bevy_module.API}/event/99103/": "event_99103.json",
}


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


async def fake_get(url: str, *, params=None) -> httpx.Response:
    return httpx.Response(200, json=fixture(RESPONSES[url]), request=httpx.Request("GET", url))


async def test_live_listing_to_in_person_event(monkeypatch):
    monkeypatch.setattr(bevy_module.http, "get", fake_get)
    adapter = REGISTRY["bevy"]

    items = await adapter.list_events({"chapter_id": 1227})
    assert [adapter.external_id(item) for item in items] == ["133425"]
    event = adapter.to_event(await adapter.fetch_detail(items[0]), {})
    assert isinstance(event, NormalizedEvent)

    assert event.title == "DevFest Lucknow 2026: Where Builders Meet"
    assert event.start_at == datetime(2026, 11, 14, 3, 30, tzinfo=UTC)
    assert event.end_at == datetime(2026, 11, 14, 11, 30, tzinfo=UTC)
    # Bevy says is_virtual_event=true here; audience_type IN_PERSON is the truth.
    assert event.mode == "offline"
    assert event.venue_name is None  # "TBA"
    assert event.address == "Lucknow, 226001"
    assert event.organizer_name == "GDG Lucknow"
    assert event.registration_url == fixture("event_133425.json")["custom_tickets_url"]
    assert event.is_free is None  # External ticketing, no Bevy tickets.
    assert "<" not in event.description and "&nbsp;" not in event.description


def test_virtual_event_is_online_and_free():
    event = REGISTRY["bevy"].to_event(fixture("event_99103.json"), {})
    assert isinstance(event, NormalizedEvent)

    assert event.mode == "online"
    assert event.start_at == datetime(2025, 8, 10, 5, 30, tzinfo=UTC)
    assert event.address is None
    assert event.is_free is True
    assert event.description.startswith("In this session, we’ll start")

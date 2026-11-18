import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from api.models.source import Source
from engine.models import NormalizedEvent
from engine.sources import REGISTRY, luma
from engine.sources.base import Rejection

FIXTURES = Path(__file__).parents[1] / "fixtures" / "luma"
CONFIG = {"latitude": 26.85, "longitude": 80.95, "categories": ["Tech"], "allowed_hosts": []}


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def response(data):
    return httpx.Response(200, json=data, request=httpx.Request("GET", luma.API))


async def test_paginated_fixtures_keep_only_tech(monkeypatch):
    records = [fixture("non_tech"), fixture("tech")]
    calls = []

    async def get(url, *, params):
        calls.append((url, dict(params)))
        if url.endswith("/event/get"):
            return response(
                next(row for row in records if row["event"]["api_id"] == params["event_api_id"])
            )
        second = "pagination_cursor" in params
        return response(
            {
                "entries": [
                    {"event": row["event"]} for row in (records if second else records[:1])
                ],
                "has_more": not second,
                "next_cursor": "next",
            }
        )

    monkeypatch.setattr(luma.http, "get", get)
    adapter = REGISTRY["luma"]
    items = await adapter.list_events(CONFIG)
    mapped = [adapter.to_event(await adapter.fetch_detail(item), CONFIG) for item in items]
    assert len(items) == 2 and mapped[0] == Rejection("category_not_allowed")
    event = mapped[1]
    assert isinstance(event, NormalizedEvent)
    assert event.external_id == adapter.external_id(items[1]) == "evt-0zi7zx9aezlPUDF"
    assert event.mode == "offline" and event.source_platform == "luma"
    assert event.start_at == datetime(2026, 11, 28, 5, tzinfo=UTC)
    assert event.end_at == datetime(2026, 11, 28, 8, tzinfo=UTC)
    assert event.lat == 26.8466937 and event.lng == 80.94616599999999
    assert event.venue_name == "Lucknow" and event.address == "Lucknow, Uttar Pradesh, India"
    assert event.organizer_name == "Harsh Gupta" and event.is_free is True
    assert event.canonical_url == event.registration_url == "https://luma.com/ne5hyjwj"
    assert event.poster_url == records[1]["event"]["cover_url"]
    assert (
        event.description == "Just a meetup of all the data engineers who are in Lucknow currently."
    )
    assert calls[:2] == [
        (
            f"{luma.API}/discover/get-paginated-events",
            {"latitude": 26.85, "longitude": 80.95, "pagination_limit": 50, **extra},
        )
        for extra in ({}, {"pagination_cursor": "next"})
    ]


@pytest.mark.parametrize("allowed", [[], ["Run Spark"], ["usr-z9I3a20Fb1c6VAq"]])
def test_host_override_uses_stable_ids(allowed):
    result = REGISTRY["luma"].to_event(fixture("non_tech"), {**CONFIG, "allowed_hosts": allowed})
    assert isinstance(result, NormalizedEvent) is (allowed == ["usr-z9I3a20Fb1c6VAq"])


def test_online_and_optional_fields():
    raw = fixture("tech")
    raw["event"].update(
        location_type="online", coordinate=None, geo_address_info=None, cover_url=None
    )
    raw.update(hosts=None, ticket_info=None, description_mirror=None)
    event = REGISTRY["luma"].to_event(raw, CONFIG)
    assert event.mode == "online" and event.lat is event.lng is event.address is None
    assert event.is_free is event.organizer_name is event.description is event.poster_url is None
    raw["event"]["location_type"] = "unknown"
    assert REGISTRY["luma"].to_event(raw, CONFIG) == Rejection("unsupported_location_type")


def test_hybrid_location_maps_to_offline():
    raw = fixture("tech")
    raw["event"]["location_type"] = "hybrid"
    event = REGISTRY["luma"].to_event(raw, CONFIG)
    assert isinstance(event, NormalizedEvent)
    assert event.mode == "offline"
    assert event.venue_name == "Lucknow"


def test_organizer_name_uses_first_three_named_hosts():
    raw = fixture("tech")
    raw["hosts"] = [
        {"api_id": "usr-blank", "name": ""},
        {"api_id": "usr-1", "name": "One"},
        {"api_id": "usr-2", "name": "Two"},
        {"api_id": "usr-3", "name": "Three"},
        {"api_id": "usr-4", "name": "Four"},
    ]
    event = REGISTRY["luma"].to_event(raw, CONFIG)
    assert isinstance(event, NormalizedEvent)
    assert event.organizer_name == "One, Two, Three"


@pytest.mark.parametrize("cursor,limit", [(None, 3), ("repeated", 3), ("new", 1)])
async def test_bad_pagination_fails_without_partial_results(monkeypatch, cursor, limit):
    async def get(url, *, params):
        return response({"entries": [], "has_more": True, "next_cursor": cursor})

    monkeypatch.setattr(luma.http, "get", get)
    monkeypatch.setattr(luma, "_MAX_PAGES", limit)
    with pytest.raises(RuntimeError):
        await REGISTRY["luma"].list_events(CONFIG)


async def test_detail_identity_must_match(monkeypatch):
    async def get(url, *, params):
        return response(fixture("non_tech"))

    monkeypatch.setattr(luma.http, "get", get)
    with pytest.raises(ValueError, match="ID differs"):
        await REGISTRY["luma"].fetch_detail(fixture("tech")["event"])


async def test_cloudflare_block_is_not_an_empty_listing(monkeypatch):
    async def get(url, *, params):
        blocked = httpx.Response(403, request=httpx.Request("GET", url))
        blocked.raise_for_status()

    monkeypatch.setattr(luma.http, "get", get)
    with pytest.raises(httpx.HTTPStatusError):
        await REGISTRY["luma"].list_events(CONFIG)


async def test_seed_is_idempotent_and_preserves_settings(db_session, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from scripts import seed_sources

    spec = next(row for row in seed_sources.INITIAL_SOURCES if row["platform"] == "luma")
    assert spec["config_json"] == CONFIG

    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(seed_sources, "SessionLocal", session)
    monkeypatch.setattr(seed_sources, "INITIAL_SOURCES", [spec])
    await seed_sources.seed()
    row = (await db_session.scalars(select(Source).where(Source.platform == "luma"))).one()
    row.config_json, row.enabled = {**CONFIG, "allowed_hosts": ["usr-custom"]}, False
    await db_session.commit()
    await seed_sources.seed()
    rows = (await db_session.scalars(select(Source).where(Source.platform == "luma"))).all()
    assert rows == [row] and row.config_json["allowed_hosts"] == ["usr-custom"] and not row.enabled

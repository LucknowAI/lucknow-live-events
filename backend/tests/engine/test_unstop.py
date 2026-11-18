import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from api.models.source import Source
from engine.models import NormalizedEvent
from engine.sources import REGISTRY, unstop
from engine.sources.base import Rejection

FIXTURES = Path(__file__).parents[1] / "fixtures" / "unstop"
TYPES = ["hackathons", "competitions", "workshops"]


def fixture(mode):
    return json.loads((FIXTURES / f"{mode}.json").read_text())


def response(data):
    return httpx.Response(200, json={"data": data}, request=httpx.Request("GET", unstop.API))


@pytest.mark.parametrize(
    "mode,start",
    [
        ("offline", datetime(2026, 3, 6, 6, 30, tzinfo=UTC)),
        ("online", datetime(2025, 9, 25, 9, 45, 38, tzinfo=UTC)),
    ],
)
async def test_fixture_to_event_uses_event_dates(monkeypatch, mode, start):
    raw = fixture(mode)
    raw["regnRequirements"] = {"start_regn_dt": "2000-01-01T00:00:00+05:30"}
    listing = {k: v for k, v in raw.items() if k != "start_date"}

    async def get(url):
        assert url == f"{unstop.API}/competition/{raw['id']}"
        return response({"competition": raw})

    monkeypatch.setattr(unstop.http, "get", get)
    adapter = REGISTRY["unstop"]
    event = adapter.to_event(await adapter.fetch_detail(listing), {})
    assert isinstance(event, NormalizedEvent)
    assert adapter.external_id(listing) == event.external_id == str(raw["id"])
    assert event.mode == mode and event.start_at == start
    assert event.end_at == datetime.fromisoformat(raw["end_date"]).astimezone(UTC)
    assert event.canonical_url == "https://unstop.com/" + raw["public_url"]
    assert event.registration_url == event.canonical_url
    assert event.organizer_name == raw["organisation"]["name"]
    assert event.is_free is True and event.source_platform == "unstop"
    assert "<" not in event.description and "&amp;" not in event.description
    assert event.address == (
        "Chandigarh University Uttar Pradesh, Unnao, Uttar Pradesh" if mode == "offline" else None
    )


async def test_pagination_all_types_and_duplicate_ids(monkeypatch):
    records, calls = [fixture("offline"), fixture("online")], []

    async def get(url, *, params):
        assert url == f"{unstop.API}/opportunity/search-result"
        calls.append(params)
        number = params["page"]
        return response(
            {
                "current_page": number,
                "data": [records[number - 1]],
                "next_page_url": "page=2" if number == 1 else None,
            }
        )

    monkeypatch.setattr(unstop.http, "get", get)
    items = await REGISTRY["unstop"].list_events({"opportunity_types": TYPES})
    assert [item["id"] for item in items] == [row["id"] for row in records]
    assert calls == [
        {"opportunity": kind, "searchTerm": "lucknow", "per_page": 50, "page": n}
        for kind in TYPES
        for n in [1, 2]
    ]
    # Complete listings need no extra network request.
    assert await REGISTRY["unstop"].fetch_detail(records[0]) is records[0]


@pytest.mark.parametrize("page,reason", [(2, "did not advance"), (1, "more than")])
async def test_pagination_is_bounded(monkeypatch, page, reason):
    async def get(url, *, params):
        return response({"current_page": page, "data": [], "next_page_url": "page=2"})

    monkeypatch.setattr(unstop.http, "get", get)
    monkeypatch.setattr(unstop, "_MAX_PAGES", 1)
    with pytest.raises(RuntimeError, match=reason):
        await REGISTRY["unstop"].list_events({"opportunity_types": ["hackathons"]})


@pytest.mark.parametrize("kinds", [[], "hackathons", ["jobs"], [{}]])
async def test_invalid_types_make_no_request(monkeypatch, kinds):
    async def get(*args, **kwargs):
        pytest.fail("Invalid config must fail before a request")

    monkeypatch.setattr(unstop.http, "get", get)
    with pytest.raises(ValueError):
        await REGISTRY["unstop"].list_events({"opportunity_types": kinds})


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"region": "global"}, "unsupported_region"),
        ({"start_date": None}, "missing_event_start"),
    ],
)
def test_missing_or_ambiguous_data_is_rejected(change, reason):
    assert REGISTRY["unstop"].to_event({**fixture("online"), **change}, {}) == Rejection(reason)


@pytest.mark.parametrize("primary", [None, {"address": "", "city": "", "lat": None}])
def test_location_fallback_and_unknown_fee(primary):
    raw = fixture("offline")
    raw.update(address_with_country_logo=primary, locations=[{"name": "Lucknow"}], paid=None)
    event = REGISTRY["unstop"].to_event(raw, {})
    assert event.address == "Lucknow" and event.is_free is None
    raw["address_with_country_logo"] = {"lat": 0, "lng": 0}
    event = REGISTRY["unstop"].to_event(raw, {})
    assert event.lat == event.lng == 0 and event.address is None


async def test_wrong_detail_id_is_rejected(monkeypatch):
    async def get(url):
        return response({"competition": fixture("online")})

    monkeypatch.setattr(unstop.http, "get", get)
    with pytest.raises(ValueError, match="ID differs"):
        await REGISTRY["unstop"].fetch_detail({"id": fixture("offline")["id"]})


@pytest.mark.parametrize(
    "old_config", [None, {"max_items": 7}, {"opportunity_types": ["workshops"]}]
)
async def test_seed_is_idempotent_and_preserves_custom_settings(
    db_session, monkeypatch, old_config
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3]))
    from scripts import seed_sources

    spec = next(row for row in seed_sources.INITIAL_SOURCES if row["platform"] == "unstop")
    if old_config is not None:
        db_session.add(
            Source(
                name=spec["name"],
                platform="unstop",
                base_url=spec["base_url"],
                enabled=False,
                status="blacklisted",
                config_json=old_config,
            )
        )
        await db_session.commit()

    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr(seed_sources, "SessionLocal", session)
    monkeypatch.setattr(seed_sources, "INITIAL_SOURCES", [spec])
    await seed_sources.seed()
    await seed_sources.seed()
    rows = (await db_session.scalars(select(Source))).all()
    assert len(rows) == 1
    expected = dict(spec["config_json"] if old_config is None else old_config)
    expected.setdefault("opportunity_types", TYPES)
    assert rows[0].config_json == expected
    assert rows[0].enabled is (old_config is None)
    assert rows[0].status == ("active" if old_config is None else "blacklisted")

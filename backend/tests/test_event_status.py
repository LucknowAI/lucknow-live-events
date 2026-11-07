from datetime import timedelta

import pytest
from sqlalchemy import func, select

from api.models.event import Event


@pytest.mark.parametrize(
    "start_hours,end_hours,status",
    [
        (1, 2, "upcoming"),
        (1, None, "upcoming"),
        (-1, 1, "ongoing"),
        (-2, -1, "past"),
        (-23, None, "ongoing"),
        (-25, None, "past"),
        (0, 1, "ongoing"),
        (-1, 0, "ongoing"),
        (-24, None, "ongoing"),
    ],
)
async def test_status_filter_default_and_detail(client, db_session, start_hours, end_hours, status):
    # PostgreSQL NOW is stable in this transaction, including exact boundary cases.
    now = await db_session.scalar(select(func.now()))
    db_session.add(
        Event(
            slug="status-event",
            title="Status event",
            canonical_url="https://example.org/event",
            start_at=now + timedelta(hours=start_hours),
            end_at=now + timedelta(hours=end_hours) if end_hours is not None else None,
            published_at=now,
        )
    )
    await db_session.commit()
    filtered = (await client.get("/api/v1/events", params={"status": status})).json()
    assert filtered["total"] == 1 and filtered["items"][0]["status"] == status
    default = (await client.get("/api/v1/events")).json()
    assert default["total"] == (0 if status == "past" else 1)
    detail = await client.get("/api/v1/events/status-event")
    assert detail.status_code == 200 and detail.json()["status"] == status


async def test_filter_pagination_visibility_and_completed_window(client, db_session):
    now = await db_session.scalar(select(func.now()))
    for slug, start, end, overrides in [
        ("future", 1, 2, {}),
        ("live", -1, 1, {}),
        ("recent", -2, -1, {}),
        ("long-running", -90, -2, {}),
        ("window-edge", -31, -30, {}),
        ("too-old", -40, -31, {}),
        ("no-end-recent", -2, None, {}),
        ("no-end-old", -32, None, {}),
        ("unpublished", -2, -1, {"published_at": None}),
        ("hidden", -2, -1, {"expires_at": now - timedelta(days=1)}),
    ]:
        values = {
            "slug": slug,
            "title": slug,
            "start_at": now + timedelta(days=start),
            "end_at": now + timedelta(days=end) if end is not None else None,
            "canonical_url": f"https://example.org/{slug}",
            "published_at": now,
        }
        db_session.add(Event(**{**values, **overrides}))
    await db_session.commit()
    default = (await client.get("/api/v1/events")).json()
    assert {row["slug"] for row in default["items"]} == {"future", "live"}
    past = (await client.get("/api/v1/events", params={"status": "past", "past_days": 30})).json()
    assert {row["slug"] for row in past["items"]} == {
        "recent",
        "long-running",
        "window-edge",
        "no-end-recent",
    }
    assert (await client.get("/api/v1/events", params={"status": "past"})).json()["total"] == 6
    pages = [
        (
            await client.get(
                "/api/v1/events",
                params={"status": "past", "past_days": 30, "limit": 2, "page": page},
            )
        ).json()
        for page in [1, 2]
    ]
    assert all(page["total"] == 4 and len(page["items"]) == 2 for page in pages)
    assert len({row["id"] for page in pages for row in page["items"]}) == 4
    legacy = (await client.get("/api/v1/events/past")).json()
    assert all(row["status"] == "past" for row in legacy)
    assert "long-running" in {row["slug"] for row in legacy}
    assert not {"live", "future", "too-old"} & {row["slug"] for row in legacy}
    assert (await client.get("/api/v1/events", params={"status": "past", "q": "recent"})).json()[
        "total"
    ] == 2


@pytest.mark.parametrize("params", [{"status": "invalid"}, {"past_days": 0}, {"past_days": 91}])
async def test_invalid_status_filters_are_rejected(client, params):
    assert (await client.get("/api/v1/events", params=params)).status_code == 422

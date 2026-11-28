from datetime import UTC, datetime, timedelta

import pytest

from api.models.event import Event

LISTS = ["/events", "/events/featured", "/events/this-week", "/events/student-friendly"]
FACETS = ["/topics", "/communities", "/localities"]


def _event(slug: str, start: timedelta, **hidden) -> Event:
    now = datetime.now(UTC)
    return Event(
        slug=slug,
        title=slug,
        canonical_url=f"https://example.org/{slug}",
        start_at=now + start,
        published_at=hidden.pop("published_at", now),
        is_featured=True,
        is_free=True,
        is_student_friendly=True,
        topics_json=["ai"],
        community_name="GDG Lucknow",
        locality="Gomti Nagar",
        **hidden,
    )


@pytest.mark.parametrize(
    "hidden",
    [
        {},  # control: the same events are shown
        {"expires_at": datetime.now(UTC) - timedelta(hours=1)},
        {"is_cancelled": True},
        {"published_at": None},
    ],
)
async def test_hidden_events_are_absent_from_every_public_endpoint(client, db_session, hidden):
    shown = not hidden
    db_session.add(_event("soon", timedelta(days=2), **dict(hidden)))
    db_session.add(_event("ended", -timedelta(days=3), **dict(hidden)))
    await db_session.commit()

    async def slugs(path):
        body = (await client.get(f"/api/v1{path}")).json()
        return {e["slug"] for e in (body["items"] if path == "/events" else body)}

    for path in LISTS:
        # Featured, like the others, never lists an event that has ended.
        assert await slugs(path) == ({"soon"} if shown else set()), path
    assert await slugs("/events/past") == ({"ended"} if shown else set())
    assert await slugs("/feeds/events.json") == ({"soon", "ended"} if shown else set())
    for path in FACETS:
        items = (await client.get(f"/api/v1{path}")).json()["items"]
        assert [i["count"] for i in items] == ([2] if shown else []), path
    ics = (await client.get("/api/v1/feeds/events.ics")).text
    assert ("soon@lucknow-tech-events" in ics) is shown
    assert (await client.get("/api/v1/events/soon")).status_code == (200 if shown else 404)

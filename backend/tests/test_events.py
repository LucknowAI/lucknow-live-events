from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.event import Event


async def test_events_list(client: AsyncClient):
    response = await client.get("/api/v1/events")
    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "limit": 20}


# Reuse the unique slug to detect committed rows leaking across test transactions.
@pytest.mark.parametrize("_run", range(2))
async def test_committed_event_is_visible(client: AsyncClient, db_session: AsyncSession, _run):
    assert (await client.get("/api/v1/events")).json()["total"] == 0
    event = Event(
        slug="test-harness-event",
        title="Test harness event",
        start_at=datetime.now(UTC) + timedelta(days=1),
        published_at=datetime.now(UTC),
        canonical_url="https://example.org/test-harness-event",
    )
    db_session.add(event)
    await db_session.commit()
    response = await client.get("/api/v1/events")
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["slug"] == event.slug

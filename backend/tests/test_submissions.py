import sys

import pytest
from httpx import AsyncClient

from api.core.deps import get_current_admin
from api.main import app


@pytest.fixture
def as_admin(monkeypatch):
    monkeypatch.setitem(app.dependency_overrides, get_current_admin, lambda: {"role": "admin"})


async def test_submission_is_stored_pending_and_listed_for_admin(client: AsyncClient, as_admin):
    url = "https://example.org/some-meetup"
    response = await client.post("/api/v1/submissions", json={"event_url": url})
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    # No Celery task or AI call: nothing from the old worker package gets loaded.
    assert not any(name == "workers" or name.startswith("workers.") for name in sys.modules)

    listed = (await client.get("/api/v1/admin/moderation/submissions")).json()
    assert [item["event_url"] for item in listed] == [url]


async def test_public_topics_still_served(client: AsyncClient):
    assert (await client.get("/api/v1/topics")).status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/admin/discovery/run",
        "/api/v1/admin/sources/crawl/run-all",
        "/api/v1/admin/events/expire-now",
        "/api/v1/admin/events/fix-bad-dates",
    ],
)
async def test_ai_and_crawl_triggers_are_gone(client: AsyncClient, as_admin, path):
    assert (await client.post(path)).status_code in (404, 405)

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

import engine.run as runner
from api.core.config import settings
from api.models.event import Event
from api.models.ingest import IngestRun
from api.models.source import Source
from engine.models import NormalizedEvent


class Fake:
    def __init__(self, pages: list[list[dict]], platform: str, fail: bool = False):
        self.pages = list(pages)
        self.platform = platform
        self.fail = fail
        self.start = datetime.now(UTC) + timedelta(days=10)

    async def list_events(self, config):
        if self.fail:
            raise RuntimeError("down")
        return self.pages.pop(0)

    def external_id(self, item):
        return item["id"]

    async def fetch_detail(self, item):
        return item

    def to_event(self, raw, config):
        return NormalizedEvent(
            external_id=raw["id"],
            title="Lucknow meetup",
            start_at=self.start,
            mode="online",
            canonical_url="https://example.com/events/1",
            source_platform=self.platform,
        )


async def add_source(session, name: str, platform: str) -> Source:
    source = Source(
        name=name,
        platform=platform,
        base_url="https://example.com",
        enabled=True,
        config_json={"lucknow_community": True},
    )
    session.add(source)
    await session.commit()
    return source


async def test_one_failure_does_not_stop_the_next_source(db_session, monkeypatch):
    boom = await add_source(db_session, "A boom", "boom")
    ok = await add_source(db_session, "B ok", "ok")
    monkeypatch.setattr(
        runner, "REGISTRY", {"boom": Fake([], "boom", fail=True), "ok": Fake([[{"id": "1"}]], "ok")}
    )

    assert await runner.run(session=db_session) == 1

    rows = list(
        await db_session.scalars(select(IngestRun).where(IngestRun.source_id.in_([boom.id, ok.id])))
    )
    status = {row.source_id: row.status for row in rows}
    assert status == {boom.id: "failed", ok.id: "success"}
    assert "down" in next(row.error for row in rows if row.source_id == boom.id)


async def test_second_run_reports_only_unchanged(db_session, monkeypatch):
    source = await add_source(db_session, "Replay", "replay")
    monkeypatch.setattr(
        runner, "REGISTRY", {"replay": Fake([[{"id": "1"}], [{"id": "1"}]], "replay")}
    )

    assert await runner.run(platform="replay", session=db_session) == 0
    assert await runner.run(platform="replay", session=db_session) == 0

    runs = list(
        await db_session.scalars(
            select(IngestRun).where(IngestRun.source_id == source.id).order_by(IngestRun.started_at)
        )
    )
    assert (runs[0].new, runs[0].unchanged) == (1, 0)
    assert (runs[1].new, runs[1].updated, runs[1].unchanged) == (0, 0, 1)
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Event).where(Event.source_platform == "replay")
        )
        == 1
    )


async def test_empty_listing_after_a_success_is_failed_and_not_saved(db_session, monkeypatch):
    source = await add_source(db_session, "Empty", "empty")
    monkeypatch.setattr(runner, "REGISTRY", {"empty": Fake([[{"id": "1"}], []], "empty")})

    assert await runner.run(platform="empty", session=db_session) == 0
    await db_session.refresh(source)
    seen = source.last_success_at

    assert await runner.run(platform="empty", session=db_session) == 1
    await db_session.refresh(source)
    failed = await db_session.scalar(
        select(IngestRun).where(IngestRun.source_id == source.id, IngestRun.status == "failed")
    )

    assert source.last_success_at == seen
    assert failed.error == "empty listing"
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Event).where(Event.source_platform == "empty")
        )
        == 1
    )


async def test_first_empty_listing_is_a_success(db_session, monkeypatch):
    source = await add_source(db_session, "Fresh", "fresh")
    monkeypatch.setattr(runner, "REGISTRY", {"fresh": Fake([[]], "fresh")})

    assert await runner.run(platform="fresh", session=db_session) == 0

    run = await db_session.scalar(select(IngestRun).where(IngestRun.source_id == source.id))
    assert (run.status, run.fetched) == ("success", 0)


async def test_empty_listing_after_the_last_event_ended_is_a_success(db_session, monkeypatch):
    source = await add_source(db_session, "Ended", "ended")
    fake = Fake([[{"id": "1"}], []], "ended")
    fake.start = datetime.now(UTC) - timedelta(hours=2)
    monkeypatch.setattr(runner, "REGISTRY", {"ended": fake})

    assert await runner.run(platform="ended", session=db_session) == 0
    assert await db_session.scalar(select(func.count()).select_from(Event)) == 1
    assert await runner.run(platform="ended", session=db_session) == 0

    latest = await db_session.scalar(
        select(IngestRun)
        .where(IngestRun.source_id == source.id)
        .order_by(IngestRun.started_at.desc())
        .limit(1)
    )
    assert (latest.status, latest.fetched) == ("success", 0)
class _FakeResponse:
    async def aclose(self) -> None:
        return None


async def test_ping_reports_success_and_failure(monkeypatch):
    calls = []

    async def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse()

    monkeypatch.setattr(runner.http, "get", fake_get)
    monkeypatch.setattr(settings, "HEALTHCHECK_URL", "https://hc.example/token")

    await runner._ping(failed=False)
    await runner._ping(failed=True)

    assert calls == [
        ("https://hc.example/token", {"log_url": False}),
        ("https://hc.example/token/fail", {"log_url": False}),
    ]

    # A blank URL disables the ping instead of calling out to an empty target.
    monkeypatch.setattr(settings, "HEALTHCHECK_URL", "")
    await runner._ping(failed=True)
    assert len(calls) == 2


async def test_a_real_run_pings_the_healthcheck(db_session, monkeypatch):
    await add_source(db_session, "B ok", "ok")
    monkeypatch.setattr(runner, "REGISTRY", {"ok": Fake([[{"id": "1"}]], "ok")})
    pings = []

    async def fake_ping(failed):
        pings.append(failed)

    monkeypatch.setattr(runner, "_ping", fake_ping)

    assert await runner.run(platform="ok", session=db_session, ping=True) == 0
    assert pings == [False]


async def test_dry_run_never_pings(db_session, monkeypatch):
    await add_source(db_session, "A boom", "boom")
    monkeypatch.setattr(runner, "REGISTRY", {"boom": Fake([], "boom", fail=True)})
    pings = []

    async def fake_ping(failed):
        pings.append(failed)

    monkeypatch.setattr(runner, "_ping", fake_ping)

    # A dry-run writes nothing, so it must not claim the daily run happened.
    assert await runner.run(platform="boom", session=db_session, dry_run=True, ping=True) == 1
    assert pings == []


async def test_run_without_a_matching_source_pings_failure(db_session, monkeypatch):
    pings = []

    async def fake_ping(failed):
        pings.append(failed)

    monkeypatch.setattr(runner, "_ping", fake_ping)

    assert await runner.run(platform="nope", session=db_session, ping=True) == 1
    assert pings == [True]

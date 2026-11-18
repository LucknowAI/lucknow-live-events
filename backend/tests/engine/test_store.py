from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.event import Event
from api.models.ingest import EventChange, IngestRun
from api.models.source import Source
from engine import store
from engine.models import NormalizedEvent

START = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


def make_event(n: int, start_at: datetime = START) -> NormalizedEvent:
    return NormalizedEvent(
        external_id=f"evt-{n}",
        title="GDG Lucknow meetup",  # Same title on purpose: slugs must still be unique.
        start_at=start_at,
        mode="offline",
        canonical_url=f"https://gdg.community.dev/events/evt-{n}",
        source_platform="bevy",
    )


@pytest.fixture
async def source(db_session: AsyncSession) -> Source:
    src = Source(name="GDG Lucknow", platform="bevy", base_url="https://gdg.community.dev")
    db_session.add(src)
    await db_session.commit()
    return src


async def new_run(db_session: AsyncSession, source: Source) -> IngestRun:
    run = IngestRun(source_id=source.id, started_at=datetime.now(UTC))
    db_session.add(run)
    await db_session.commit()
    return run


async def count(db_session: AsyncSession, model) -> int:
    return await db_session.scalar(select(func.count()).select_from(model))


async def test_second_save_of_same_events_writes_nothing(db_session, source):
    events = [make_event(n) for n in range(3)]
    ids = [e.external_id for e in events]

    first = await store.save(db_session, source, await new_run(db_session, source), events, ids)
    run = await new_run(db_session, source)
    second = await store.save(db_session, source, run, events, ids)

    assert first == {"new": 3, "updated": 0, "unchanged": 0}
    assert second == {"new": 0, "updated": 0, "unchanged": 3}
    assert await count(db_session, EventChange) == 0
    rows = (await db_session.scalars(select(Event))).all()
    assert len({row.slug for row in rows}) == 3
    assert {row.last_seen_at for row in rows} == {run.started_at}


async def test_changed_time_is_updated_with_one_change_row(db_session, source):
    events = [make_event(n) for n in range(3)]
    ids = [e.external_id for e in events]
    await store.save(db_session, source, await new_run(db_session, source), events, ids)

    later = START + timedelta(hours=2)
    events[1] = make_event(1, start_at=later)
    result = await store.save(db_session, source, await new_run(db_session, source), events, ids)

    assert result == {"new": 0, "updated": 1, "unchanged": 2}
    change = await db_session.scalar(select(EventChange))
    assert change.diff == {"start_at": [str(START), str(later)]}


async def test_failure_midway_saves_nothing(db_session, source, monkeypatch):
    real_slug = store._unique_slug
    calls = 0

    async def fail_on_third(session, title):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("boom")
        return await real_slug(session, title)

    monkeypatch.setattr(store, "_unique_slug", fail_on_third)
    events = [make_event(n) for n in range(3)]
    with pytest.raises(RuntimeError):
        await store.save(
            db_session,
            source,
            await new_run(db_session, source),
            events,
            [e.external_id for e in events],
        )

    assert await count(db_session, Event) == 0

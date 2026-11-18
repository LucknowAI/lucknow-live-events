from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from api.models.event import Event
from api.models.ingest import IngestRun
from api.models.source import Source
from engine import cleanup, store
from engine.models import NormalizedEvent


def now() -> datetime:
    return datetime.now(UTC)


def add_event(session, source, name, *, start_at, end_at=None, last_seen_at=None) -> Event:
    row = Event(
        slug=name,
        title=name,
        start_at=start_at,
        end_at=end_at,
        canonical_url=f"https://example.com/{name}",
        source_platform=source.platform,
        source_id=source.id,
        external_id=name,
        last_seen_at=last_seen_at,
        published_at=start_at,
    )
    session.add(row)
    return row


def add_run(session, source, started_at, status) -> None:
    session.add(IngestRun(source_id=source.id, started_at=started_at, status=status))


async def test_old_events_are_deleted_and_recent_ones_stay(db_session):
    source = Source(name="GDG", platform="bevy", base_url="https://example.com")
    db_session.add(source)
    await db_session.commit()
    moment = now()
    add_event(
        db_session,
        source,
        "old",
        end_at=moment - timedelta(days=31),
        start_at=moment - timedelta(days=32),
    )
    add_event(db_session, source, "no-end", start_at=moment - timedelta(days=31))
    add_event(
        db_session,
        source,
        "recent",
        end_at=moment - timedelta(days=29),
        start_at=moment - timedelta(days=30),
    )
    await db_session.commit()

    await cleanup.apply(db_session)

    left = set(await db_session.scalars(select(Event.external_id)))
    assert left == {"recent"}


async def test_two_successful_misses_hide_and_a_failure_does_not(db_session):
    gone = Source(name="Gone", platform="bevy", base_url="https://example.com")
    flaky = Source(name="Flaky", platform="bevy", base_url="https://example.com/flaky")
    db_session.add_all([gone, flaky])
    await db_session.commit()
    seen = now() - timedelta(days=3)
    later = now() + timedelta(days=10)
    hidden = add_event(db_session, gone, "hidden", start_at=later, end_at=later, last_seen_at=seen)
    kept = add_event(db_session, flaky, "kept", start_at=later, end_at=later, last_seen_at=seen)
    add_run(db_session, gone, seen + timedelta(days=1), "success")
    add_run(db_session, gone, seen + timedelta(days=2), "success")
    add_run(db_session, flaky, seen + timedelta(days=1), "success")
    add_run(db_session, flaky, seen + timedelta(days=2), "failed")
    await db_session.commit()

    await cleanup.apply(db_session)
    await db_session.refresh(hidden)
    await db_session.refresh(kept)

    assert hidden.expires_at is not None and hidden.is_cancelled is False
    assert kept.expires_at is None


async def test_ended_event_is_not_hidden_by_misses(db_session):
    source = Source(name="GDG", platform="bevy", base_url="https://example.com")
    db_session.add(source)
    await db_session.commit()
    seen = now() - timedelta(days=3)
    moment = now()
    row = add_event(
        db_session,
        source,
        "over",
        start_at=moment - timedelta(days=2),
        end_at=moment - timedelta(days=1),
        last_seen_at=seen,
    )
    add_run(db_session, source, seen + timedelta(days=1), "success")
    add_run(db_session, source, seen + timedelta(days=2), "success")
    await db_session.commit()

    await cleanup.apply(db_session)
    await db_session.refresh(row)

    assert row.expires_at is None


async def test_seen_again_clears_expiry(db_session):
    source = Source(name="GDG", platform="bevy", base_url="https://example.com")
    db_session.add(source)
    await db_session.commit()
    event = NormalizedEvent(
        external_id="evt-1",
        title="GDG Lucknow meetup",
        start_at=now() + timedelta(days=10),
        mode="offline",
        canonical_url="https://example.com/evt-1",
        source_platform="bevy",
    )
    moment = now()
    await store.save(
        db_session,
        source,
        IngestRun(source_id=source.id, started_at=moment),
        [event],
        [event.external_id],
    )
    row = await db_session.scalar(select(Event))
    row.expires_at = moment
    await db_session.commit()

    await store.save(
        db_session,
        source,
        IngestRun(source_id=source.id, started_at=moment + timedelta(hours=1)),
        [event],
        [event.external_id],
    )
    await db_session.refresh(row)

    assert row.expires_at is None

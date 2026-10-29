"""The only engine module that writes to the database.

One transaction per source: new events are inserted, changed ones updated with
an `event_changes` row, unchanged ones not written. Running the same batch
twice writes no event content the second time.
"""

import json
from typing import Any

from slugify import slugify
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.event import Event
from api.models.ingest import EventChange, IngestRun
from api.models.source import Source
from engine import delta
from engine.models import NormalizedEvent


def _columns(event: NormalizedEvent) -> dict[str, Any]:
    values = event.model_dump()
    if values["is_free"] is None:
        values["is_free"] = True  # Column is NOT NULL; same default as the column.
    return values


async def _unique_slug(session: AsyncSession, title: str) -> str:
    # Autoflush makes events added earlier in this batch visible to the check.
    base = slugify(title, max_length=280) or "event"
    slug, n = base, 1
    while await session.scalar(select(Event.id).where(Event.slug == slug)):
        slug, n = f"{base}-{n}", n + 1
    return slug


async def save(
    session: AsyncSession,
    source: Source,
    run: IngestRun,
    events: list[NormalizedEvent],
    listed_ids: list[str],
) -> dict[str, int]:
    """Save one source's events and mark every listed id as seen.

    `listed_ids` comes from the listing, so an event whose detail fetch failed
    still counts as seen. Any error rolls back the whole batch. Set
    `run.started_at` in Python when creating the run: a DB default is not
    loaded on the object, and reading it here would fail.
    """
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    try:
        rows = await session.scalars(
            select(Event).where(
                Event.source_platform == source.platform,
                Event.external_id.in_([e.external_id for e in events]),
            )
        )
        stored = {row.external_id: row for row in rows}

        for event in events:
            row = stored.get(event.external_id)
            kind = delta.classify(event, row.content_hash if row else None)
            if kind == "unchanged":
                counts["unchanged"] += 1
                continue

            values = _columns(event)
            if kind == "new":
                row = Event(
                    **values,
                    slug=await _unique_slug(session, event.title),
                    source_id=source.id,  # First writer wins; never changed on update.
                    published_at=run.started_at,
                )
                session.add(row)
                # A platform listing the same id twice must not insert it twice.
                stored[event.external_id] = row
                counts["new"] += 1
            else:
                changes = delta.diff({field: getattr(row, field) for field in values}, values)
                for field, value in values.items():
                    setattr(row, field, value)
                session.add(
                    EventChange(
                        event_id=row.id,
                        run_id=run.id,
                        diff=json.loads(json.dumps(changes, default=str)),
                    )
                )
                counts["updated"] += 1
            row.content_hash = delta.content_hash(event)

        if listed_ids:
            await session.execute(
                update(Event)
                .where(Event.source_platform == source.platform, Event.external_id.in_(listed_ids))
                # Setting updated_at to itself skips its onupdate: being seen is not an edit.
                .values(last_seen_at=run.started_at, updated_at=Event.updated_at)
            )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return counts

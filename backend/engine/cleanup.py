"""Delete old events and hide ones a source has stopped listing.

Called at the end of a real engine run. A failed run does not count as a miss.
Ended events are not hidden: live listings drop them immediately, so counting
those misses would take them off the site in two days instead of 30.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.event import Event
from api.models.ingest import IngestRun

KEEP_DAYS = 30
MISSES_TO_HIDE = 2


def _ended():
    # No end date: the start is when the event is over.
    return func.coalesce(Event.end_at, Event.start_at)


async def apply(session: AsyncSession) -> None:
    now = datetime.now(UTC)
    ended = _ended()
    # Count this event's own source (the first writer). last_seen is shared by
    # (platform, external_id), so the other chapter listing a cohosted event
    # resets the clock. A null last_seen is not a miss.
    misses = (
        select(func.count())
        .where(
            IngestRun.source_id == Event.source_id,
            IngestRun.status == "success",
            IngestRun.started_at > Event.last_seen_at,
        )
        .scalar_subquery()
    )
    await session.execute(delete(Event).where(ended < now - timedelta(days=KEEP_DAYS)))
    await session.execute(
        update(Event)
        .where(
            Event.source_id.is_not(None),
            Event.last_seen_at.is_not(None),
            ended >= now,
            misses >= MISSES_TO_HIDE,
        )
        # Hiding is not an edit, so leave updated_at alone.
        .values(expires_at=now, updated_at=Event.updated_at)
    )
    await session.commit()

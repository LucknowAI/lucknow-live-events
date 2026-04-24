from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.event import Event


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def get_event_by_slug(db: AsyncSession, slug: str) -> Event | None:
    stmt = select(Event).where(Event.slug == slug)
    res = await db.execute(stmt)
    return res.scalar_one_or_none()


async def list_events(
    db: AsyncSession,
    *,
    q: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    mode: str | None = None,
    event_type: str | None = None,
    topic: str | None = None,
    locality: str | None = None,
    community: str | None = None,
    is_free: bool | None = None,
    is_student_friendly: bool | None = None,
    page: int = 1,
    limit: int = 20,
) -> tuple[list[Event], int]:
    # MVP implementation: basic filters + ordering. Full-text search wiring comes later.
    now = _utc_now()
    stmt = select(Event).where((Event.published_at.is_not(None)) & (Event.expires_at.is_(None) | (Event.expires_at > now)))

    if start_date is not None:
        stmt = stmt.where(Event.start_at >= datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc))
    if end_date is not None:
        stmt = stmt.where(Event.start_at <= datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc))
    if mode:
        stmt = stmt.where(Event.mode == mode)
    if event_type:
        stmt = stmt.where(Event.event_type == event_type)
    if locality:
        stmt = stmt.where(func.lower(Event.locality) == locality.lower())
    if community:
        like_c = f"%{community}%"
        stmt = stmt.where(Event.community_name.ilike(like_c))
    if is_free is not None:
        stmt = stmt.where(Event.is_free == is_free)
    if is_student_friendly is not None:
        stmt = stmt.where(Event.is_student_friendly == is_student_friendly)
    if topic:
        stmt = stmt.where(Event.topics_json.contains([topic]))
    if q:
        tsq = func.plainto_tsquery("english", q)
        stmt = stmt.where(
            or_(
                Event.search_vector.op("@@")(tsq),
                Event.title.ilike(f"%{q}%"),
                Event.short_description.ilike(f"%{q}%"),
                Event.community_name.ilike(f"%{q}%"),
            )
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await db.execute(count_stmt)).scalar_one()

    # Sort: real-dated events first (by date ASC), then TBA events (by created_at DESC)
    stmt = stmt.order_by(Event.date_tba.asc(), Event.start_at.asc()).offset((page - 1) * limit).limit(limit)
    items = (await db.execute(stmt)).scalars().all()
    return items, int(total)


async def list_featured(db: AsyncSession, max_items: int = 5) -> list[Event]:
    stmt = (
        select(Event)
        .where((Event.is_featured == True) & (Event.published_at.is_not(None)))  # noqa: E712
        .order_by(Event.start_at.asc())
        .limit(max_items)
    )
    return (await db.execute(stmt)).scalars().all()


async def list_calendar_events(db: AsyncSession, start_date: date | None = None, end_date: date | None = None) -> list[Event]:
    """Return events for the calendar view — excludes date_tba events."""
    now = _utc_now()
    stmt = select(Event).where(
        (Event.published_at.is_not(None))
        & (Event.expires_at.is_(None) | (Event.expires_at > now))
        & (Event.date_tba == False)  # noqa: E712
        & (Event.start_at >= now)
    )
    if start_date is not None:
        stmt = stmt.where(Event.start_at >= datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc))
    if end_date is not None:
        stmt = stmt.where(Event.start_at <= datetime.combine(end_date, datetime.max.time(), tzinfo=timezone.utc))
    stmt = stmt.order_by(Event.start_at.asc())
    return (await db.execute(stmt)).scalars().all()


async def list_this_week(db: AsyncSession) -> list[Event]:
    now = _utc_now()
    stmt = (
        select(Event)
        .where((Event.published_at.is_not(None)) & (Event.start_at >= now) & (Event.start_at <= now + timedelta(days=7)))
        .order_by(Event.start_at.asc())
    )
    return (await db.execute(stmt)).scalars().all()


async def list_student_friendly(db: AsyncSession) -> list[Event]:
    now = _utc_now()
    stmt = (
        select(Event)
        .where(
            (Event.published_at.is_not(None))
            & (Event.start_at >= now)
            & (Event.start_at <= now + timedelta(days=30))
            & (Event.is_student_friendly == True)  # noqa: E712
            & (Event.is_free == True)  # noqa: E712
        )
        .order_by(Event.start_at.asc())
    )
    return (await db.execute(stmt)).scalars().all()


async def list_past_events(db: AsyncSession, days: int = 30) -> list[Event]:
    """Return published events that completed within the last `days` days.

    Shown in the "Completed Events" section on the frontend.
    Ordered most-recently-completed first. Excludes date_tba events.
    """
    now = _utc_now()
    cutoff = now - timedelta(days=days)
    stmt = (
        select(Event)
        .where(
            (Event.published_at.is_not(None))
            & (Event.date_tba == False)  # noqa: E712
            & (Event.start_at < now)
            & (Event.start_at >= cutoff)
        )
        .order_by(Event.start_at.desc())
    )
    return (await db.execute(stmt)).scalars().all()

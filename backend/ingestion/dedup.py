from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.event import Event


def _norm(s: str | None) -> str:
    if not s:
        return ""
    return s.lower().strip()


def _date_bucket(dt: datetime | None) -> str:
    """Truncate to 12-hour buckets for fuzzy date matching."""
    if dt is None:
        return ""
    # Round down to nearest 12h
    bucket_hour = (dt.hour // 12) * 12
    return dt.strftime(f"%Y-%m-%d-{bucket_hour:02d}")


def dedupe_key(title: str | None, start_at: datetime | None, organizer: str | None) -> str:
    raw = _norm(title) + "|" + _date_bucket(start_at) + "|" + _norm(organizer)
    return hashlib.sha256(raw.encode()).hexdigest()


async def find_duplicate(
    db: AsyncSession,
    title: str | None,
    start_at: datetime | None,
    organizer: str | None,
    url: str | None = None,
) -> Event | None:
    """Return an existing Event with the same dedupe key, or None."""
    
    # 1. Exact URL match is always a duplicate
    if url:
        stmt = select(Event).filter(Event.canonical_url == url).limit(1)
        res = await db.execute(stmt)
        dup = res.scalar_one_or_none()
        if dup:
            return dup

    # 2. Check for events with same title (case-insensitive) within ±12h.
    if title and start_at:
        window_start = start_at - timedelta(hours=12)
        window_end = start_at + timedelta(hours=12)
        stmt = (
            select(Event)
            .where(
                (Event.title.ilike(f"%{_norm(title)}%"))
                & (Event.start_at >= window_start)
                & (Event.start_at <= window_end)
            )
            .limit(1)
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none()
        
    # 3. If no start_at exists but Title is completely identical
    if title and not start_at:
        stmt = select(Event).where(Event.title.ilike(title)).limit(1)
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    return None

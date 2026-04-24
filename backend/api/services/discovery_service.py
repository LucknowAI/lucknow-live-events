from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.event import Event


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _published_filter():
    now = _utc_now()
    return (Event.published_at.is_not(None)) & (Event.expires_at.is_(None) | (Event.expires_at > now))


async def list_topics_with_counts(db: AsyncSession, *, limit: int = 200) -> list[tuple[str, int]]:
    stmt = select(Event.topics_json).where(_published_filter())
    rows = (await db.execute(stmt)).all()
    counts: Counter[str] = Counter()
    for (topics_json,) in rows:
        if not topics_json:
            continue
        for t in topics_json:
            if isinstance(t, str):
                name = t.strip()
                if name:
                    counts[name] += 1
    ordered = sorted(counts.items(), key=lambda x: (-x[1], x[0].lower()))
    return ordered[:limit]


async def list_communities_with_counts(db: AsyncSession, *, limit: int = 200) -> list[tuple[str, int]]:
    stmt = (
        select(Event.community_name, func.count())
        .where(_published_filter())
        .where(Event.community_name.is_not(None))
        .where(func.trim(Event.community_name) != "")
        .group_by(Event.community_name)
        .order_by(func.count().desc(), Event.community_name.asc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [(str(name), int(cnt)) for name, cnt in rows if name]


async def list_localities_with_counts(db: AsyncSession, *, limit: int = 200) -> list[tuple[str, int]]:
    stmt = (
        select(Event.locality, func.count())
        .where(_published_filter())
        .where(Event.locality.is_not(None))
        .where(func.trim(Event.locality) != "")
        .group_by(Event.locality)
        .order_by(func.count().desc(), Event.locality.asc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    return [(str(loc), int(cnt)) for loc, cnt in rows if loc]

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.event import Event
from api.models.moderation import ModerationQueueItem
from api.models.submission import ManualSubmission
from api.models.source import Source



# ─── Sources ─────────────────────────────────────────────────────────────────

async def list_sources(db: AsyncSession) -> list[Source]:
    res = await db.execute(select(Source).order_by(Source.created_at.desc()))
    return res.scalars().all()


async def get_source(db: AsyncSession, source_id: str) -> Source | None:
    return await db.get(Source, source_id)


async def create_source(db: AsyncSession, data: dict) -> Source:
    src = Source(**data)
    db.add(src)
    await db.commit()
    await db.refresh(src)
    return src


async def patch_source(db: AsyncSession, source_id: str, data: dict) -> Source | None:
    src = await db.get(Source, source_id)
    if src is None:
        return None
    for k, v in data.items():
        if v is not None:
            setattr(src, k, v)
    await db.commit()
    await db.refresh(src)
    return src


async def set_source_status(db: AsyncSession, source_id: str, status: str) -> Source | None:
    """Set source status to 'active', 'whitelisted', or 'blacklisted'.

    - whitelisted: trust_score boosted to 0.95, always enabled
    - blacklisted: disabled immediately, trust_score set to 0.0
    - active: restored to default trust_score=0.70, re-enabled
    """
    src = await db.get(Source, source_id)
    if src is None:
        return None
    src.status = status
    if status == "whitelisted":
        src.trust_score = 0.95
        src.enabled = True
    elif status == "blacklisted":
        src.trust_score = 0.0
        src.enabled = False
    elif status == "active":
        if src.trust_score == 0.0:
            src.trust_score = 0.70
        src.enabled = True
    await db.commit()
    await db.refresh(src)
    return src


async def delete_source(db: AsyncSession, source_id: str) -> bool:
    src = await db.get(Source, source_id)
    if src is None:
        return False
    await db.delete(src)
    await db.commit()
    return True


# ─── Moderation ──────────────────────────────────────────────────────────────

async def resolve_moderation(db: AsyncSession, item_id: str, decision: str) -> ModerationQueueItem | None:
    item = await db.get(ModerationQueueItem, item_id)
    if item is None:
        return None
    item.status = decision  # "approved" or "rejected"
    item.resolved_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(item)
    return item


# ─── Events (admin actions) ───────────────────────────────────────────────────

async def list_all_events(
    db: AsyncSession,
    page: int = 1,
    limit: int = 50,
    q: str | None = None,
) -> tuple[list[Event], int]:
    """List ALL events for admin (including unpublished/expired). Supports search."""
    stmt = select(Event)
    count_stmt = select(func.count()).select_from(Event)

    if q:
        like = f"%{q}%"
        stmt = stmt.where(Event.title.ilike(like))
        count_stmt = count_stmt.where(Event.title.ilike(like))

    total = (await db.execute(count_stmt)).scalar_one()
    items = (
        await db.execute(
            stmt.order_by(Event.start_at.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
    ).scalars().all()
    return list(items), int(total)


async def update_event(db: AsyncSession, event_id: str, data: dict[str, Any]) -> Event | None:
    """Full update of event data from admin."""
    ev = await db.get(Event, event_id)
    if ev is None:
        return None
    allowed_fields = {
        "title", "description", "short_description",
        "start_at", "end_at", "timezone",
        "city", "locality", "venue_name", "address", "lat", "lng",
        "mode", "event_type",
        "organizer_name", "community_name",
        "canonical_url", "registration_url", "poster_url",
        "price_type", "is_free", "is_featured", "is_cancelled",
        "topics_json", "audience_json",
    }
    for k, v in data.items():
        if k in allowed_fields and v is not None:
            setattr(ev, k, v)
    ev.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(ev)
    return ev


async def feature_event(db: AsyncSession, event_id: str, featured: bool) -> Event | None:
    ev = await db.get(Event, event_id)
    if ev is None:
        return None
    ev.is_featured = featured
    await db.commit()
    await db.refresh(ev)
    return ev


async def cancel_event(db: AsyncSession, event_id: str) -> Event | None:
    ev = await db.get(Event, event_id)
    if ev is None:
        return None
    ev.is_cancelled = True
    await db.commit()
    await db.refresh(ev)
    return ev


async def delete_event(db: AsyncSession, event_id: str) -> bool:
    ev = await db.get(Event, event_id)
    if ev is None:
        return False
    await db.delete(ev)
    await db.commit()
    return True


# ─── Stats ────────────────────────────────────────────────────────────────────

async def get_stats(db: AsyncSession) -> dict:
    now = datetime.now(timezone.utc)
    week_ahead = now + timedelta(days=7)

    events_total = (
        await db.execute(select(func.count()).select_from(Event).where(Event.published_at.is_not(None)))
    ).scalar_one()

    events_this_week = (
        await db.execute(
            select(func.count()).select_from(Event).where(
                (Event.published_at.is_not(None))
                & (Event.start_at >= now)
                & (Event.start_at <= week_ahead)
            )
        )
    ).scalar_one()

    pending_moderation = (
        await db.execute(
            select(func.count()).select_from(ModerationQueueItem).where(
                ModerationQueueItem.status == "pending"
            )
        )
    ).scalar_one()

    sources_active = (
        await db.execute(select(func.count()).select_from(Source).where(Source.enabled == True))  # noqa: E712
    ).scalar_one()

    sources_blacklisted = (
        await db.execute(select(func.count()).select_from(Source).where(Source.status == "blacklisted"))
    ).scalar_one()

    return {
        "events_total": int(events_total),
        "events_this_week": int(events_this_week),
        "pending_moderation": int(pending_moderation),
        "sources_active": int(sources_active),
        "sources_blacklisted": int(sources_blacklisted),
    }


async def list_pending_event_submissions(db: AsyncSession) -> list[dict]:
    """Event links submitted by the public, oldest first."""
    res = await db.execute(
        select(ManualSubmission)
        .where(
            (ManualSubmission.submission_type == "event")
            & (ManualSubmission.status == "pending")
        )
        .order_by(ManualSubmission.created_at.asc())
    )
    return [
        {"id": str(item.id), "event_url": item.event_url, "created_at": item.created_at}
        for item in res.scalars().all()
    ]


# ─── Community Submissions (admin moderation) ─────────────────────────────────

async def list_community_submissions(db: AsyncSession, status: str = "pending") -> list[Any]:
    """List community link submissions awaiting review."""
    from api.models.submission import ManualSubmission
    res = await db.execute(
        select(ManualSubmission)
        .where(
            (ManualSubmission.submission_type == "community")
            & (ManualSubmission.status == status)
        )
        .order_by(ManualSubmission.created_at.asc())
    )
    items = res.scalars().all()
    return [
        {
            "id": str(item.id),
            "community_name": item.community_name,
            "community_url": item.community_url,
            "community_description": item.community_description,
            "submitter_name": item.submitter_name,
            "submitter_email": item.submitter_email,
            "notes": item.notes,
            "status": item.status,
            "created_at": item.created_at,
        }
        for item in items
    ]


async def resolve_community_submission(
    db: AsyncSession, submission_id: str, decision: str
) -> Any | None:
    """Approve or reject a community submission."""
    from api.models.submission import ManualSubmission
    item = await db.get(ManualSubmission, submission_id)
    if item is None or item.submission_type != "community":
        return None
    item.status = decision  # "approved" | "rejected"
    await db.commit()
    await db.refresh(item)
    return {
        "id": str(item.id),
        "community_name": item.community_name,
        "community_url": item.community_url,
        "status": item.status,
    }


async def create_community_submission(db: AsyncSession, data: dict) -> Any:
    """Create a community link submission (from the public form or admin seeding)."""
    from api.models.submission import ManualSubmission
    item = ManualSubmission(
        submission_type="community",
        community_name=data.get("community_name"),
        community_url=data.get("community_url"),
        community_description=data.get("community_description"),
        submitter_name=data.get("submitter_name"),
        submitter_email=data.get("submitter_email"),
        notes=data.get("notes"),
        status="pending",
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return {"id": str(item.id), "status": item.status}

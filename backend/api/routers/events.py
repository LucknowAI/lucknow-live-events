from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.schemas.event import EventDetailResponse, EventListResponse
from api.services import event_service


router = APIRouter()


@router.get("", response_model=EventListResponse)
async def list_events(
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
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    items, total = await event_service.list_events(
        db,
        q=q,
        start_date=start_date,
        end_date=end_date,
        mode=mode,
        event_type=event_type,
        topic=topic,
        locality=locality,
        community=community,
        is_free=is_free,
        is_student_friendly=is_student_friendly,
        page=page,
        limit=limit,
    )
    return EventListResponse(items=items, page=page, limit=limit, total=total)


@router.get("/featured", response_model=list[EventDetailResponse])
async def featured(db: AsyncSession = Depends(get_db)):
    return await event_service.list_featured(db, max_items=5)


@router.get("/this-week", response_model=list[EventDetailResponse])
async def this_week(db: AsyncSession = Depends(get_db)):
    return await event_service.list_this_week(db)


@router.get("/student-friendly", response_model=list[EventDetailResponse])
async def student_friendly(db: AsyncSession = Depends(get_db)):
    return await event_service.list_student_friendly(db)


@router.get("/past", response_model=list[EventDetailResponse])
async def past_events(
    days: int = Query(30, ge=1, le=90, description="How many days back to look"),
    db: AsyncSession = Depends(get_db),
):
    """Return recently completed events (last N days) for the Completed Events section."""
    return await event_service.list_past_events(db, days=days)


@router.get("/{slug}", response_model=EventDetailResponse)
async def get_event(slug: str, db: AsyncSession = Depends(get_db)):
    event = await event_service.get_event_by_slug(db, slug)
    if event is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Event not found")
    return event


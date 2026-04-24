from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.core.deps import get_current_admin
from api.schemas.admin import (
    AdminEventListResponse,
    EventUpdate,
)
from api.schemas.event import EventDetailResponse
from api.services import admin_service


router = APIRouter()
Admin = Annotated[dict, Depends(get_current_admin)]


@router.get("", response_model=AdminEventListResponse)
async def list_all_events(
    admin: Admin,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List ALL events (including unpublished/expired) for admin review."""
    items, total = await admin_service.list_all_events(db, page=page, limit=limit, q=q)
    return AdminEventListResponse(items=items, page=page, limit=limit, total=total)


@router.put("/{event_id}", response_model=EventDetailResponse)
async def update_event(
    event_id: str,
    payload: EventUpdate,
    admin: Admin,
    db: AsyncSession = Depends(get_db),
):
    """Full update of event details from admin dashboard."""
    ev = await admin_service.update_event(
        db, event_id, {k: v for k, v in payload.model_dump().items() if v is not None}
    )
    if ev is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return ev


@router.post("/{event_id}/rescrape", status_code=202)
async def rescrape_event(
    event_id: str,
    admin: Admin,
    db: AsyncSession = Depends(get_db),
):
    """Re-extract a single event directly from its canonical URL via Gemini Search Grounding.
    
    This does NOT re-crawl the source listing — it targets the specific event page,
    fetches it, and runs the AI extraction pipeline in-place to update dates/images/venue.
    """
    from api.models.event import Event

    ev = await db.get(Event, event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="Event not found")

    from workers.tasks.crawl import rescrape_single_event
    task = rescrape_single_event.delay(event_id)
    return {"task_id": task.id, "event_id": event_id, "url": ev.canonical_url}


@router.post("/fix-bad-dates", status_code=202)
async def fix_bad_dates(
    admin: Admin,
    db: AsyncSession = Depends(get_db),
):
    """Batch re-extract all events with junk placeholder dates (start_at > year 2050).
    
    Events that still have no real date after re-extraction are deleted automatically.
    """
    _, count = await admin_service.get_bad_date_events(db)
    from workers.tasks.crawl import rescrape_bad_dates
    task = rescrape_bad_dates.delay()
    return {
        "task_id": task.id,
        "events_queued": count,
        "message": f"Re-extracting {count} events with junk placeholder dates",
    }


@router.post("/expire-now", status_code=202)
async def expire_now(admin: Admin):
    """Manually trigger the expiry cleanup task (runs all 3 passes: end_at, stale start_at, delete 2099)."""
    from workers.tasks.crawl import expire_past_events
    task = expire_past_events.delay()
    return {"task_id": task.id, "message": "Expiry cleanup queued"}


@router.patch("/{event_id}/feature", response_model=EventDetailResponse)
async def feature_event(
    event_id: str,
    featured: bool = True,
    admin: Admin = None,
    db: AsyncSession = Depends(get_db),
):
    ev = await admin_service.feature_event(db, event_id, featured)
    if ev is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return ev


@router.patch("/{event_id}/cancel", response_model=EventDetailResponse)
async def cancel_event(
    event_id: str,
    admin: Admin = None,
    db: AsyncSession = Depends(get_db),
):
    ev = await admin_service.cancel_event(db, event_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return ev


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(
    event_id: str,
    admin: Admin = None,
    db: AsyncSession = Depends(get_db),
):
    deleted = await admin_service.delete_event(db, event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Event not found")


# ─── Pipeline Queue ───────────────────────────────────────────────────────────

@router.get("/queue", response_model=list[dict])
async def list_event_queue(
    admin: Admin,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List raw_events currently in the pipeline queue (pending/moderation/normalized)."""
    return await admin_service.list_event_queue(db, limit=limit)


@router.get("/queue/last-published", response_model=dict | None)
async def get_last_published(
    admin: Admin,
    db: AsyncSession = Depends(get_db),
):
    """Return the most recently published event."""
    return await admin_service.get_last_published_event(db)


@router.delete("/queue/{raw_event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_queue(
    raw_event_id: str,
    admin: Admin,
    db: AsyncSession = Depends(get_db),
):
    """Hard-delete a raw_event from the pipeline queue. Also removes moderation entries."""
    removed = await admin_service.remove_from_queue(db, raw_event_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Queue item not found")


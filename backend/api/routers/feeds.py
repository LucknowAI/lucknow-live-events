from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from icalendar import Calendar, Event as IcsEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.models.event import Event


router = APIRouter()


@router.get("/events.json")
async def events_json(db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    stmt = (
        select(Event)
        .where((Event.published_at.is_not(None)) & (Event.expires_at.is_(None) | (Event.expires_at > now)))
        .order_by(Event.start_at.asc())
    )
    events = (await db.execute(stmt)).scalars().all()
    return events


@router.get("/events.ics")
async def events_ics(db: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    stmt = (
        select(Event)
        .where((Event.published_at.is_not(None)) & (Event.expires_at.is_(None) | (Event.expires_at > now)))
        .order_by(Event.start_at.asc())
    )
    events = (await db.execute(stmt)).scalars().all()

    cal = Calendar()
    cal.add("prodid", "-//Lucknow Tech Events//lucknowdevs//EN")
    cal.add("version", "2.0")

    for e in events:
        ve = IcsEvent()
        ve.add("uid", f"{e.slug}@lucknow-tech-events")
        ve.add("summary", e.title)
        ve.add("dtstart", e.start_at)
        if e.end_at is not None:
            ve.add("dtend", e.end_at)
        desc = (e.short_description or "") + f"\n\nRegister: {e.registration_url or e.canonical_url}"
        ve.add("description", desc.strip())
        if e.mode in ("online", "hybrid"):
            ve.add("location", "Online")
        else:
            loc = ", ".join([p for p in [e.venue_name, e.address] if p])
            ve.add("location", loc or "Lucknow")
        ve.add("url", e.canonical_url)
        organizer = e.community_name or e.organizer_name
        if organizer:
            ve.add("organizer", organizer)
        cal.add_component(ve)

    return Response(content=cal.to_ical(), media_type="text/calendar")


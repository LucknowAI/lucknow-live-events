from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.crawl import CrawlRun
from api.models.event import Event
from api.models.raw_event import RawEvent
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


async def list_crawl_runs(db: AsyncSession, limit: int = 50) -> list[CrawlRun]:
    res = await db.execute(select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(limit))
    return res.scalars().all()


# ─── Moderation ──────────────────────────────────────────────────────────────

async def list_pending_moderation(db: AsyncSession) -> list[dict]:
    """List pending moderation items, enriched with raw_event preview data."""
    from api.models.raw_event import RawEvent

    res = await db.execute(
        select(ModerationQueueItem)
        .where(ModerationQueueItem.status == "pending")
        .order_by(ModerationQueueItem.created_at.asc())
    )
    items = res.scalars().all()

    enriched: list[dict] = []
    for item in items:
        record: dict = {
            "id": str(item.id),
            "entity_type": item.entity_type,
            "entity_id": str(item.entity_id) if item.entity_id else None,
            "reason": item.reason,
            "severity": item.severity,
            "status": item.status,
            "ai_verdict": item.ai_verdict,
            "notes": item.notes,
            "created_at": item.created_at,
            # preview fields (filled below)
            "preview_title": None,
            "preview_url": None,
            "preview_community": None,
            "preview_confidence": None,
        }

        if item.entity_type == "raw_event" and item.entity_id:
            try:
                raw = await db.get(RawEvent, str(item.entity_id))
                if raw:
                    extracted = raw.ai_extracted_json or {}
                    payload = raw.raw_payload_json or {}

                    raw_title = (
                        extracted.get("title")
                        or payload.get("title")
                        or "(untitled)"
                    )
                    # Strip any residual HTML tags and truncate
                    import re as _re
                    clean_title = _re.sub(r"<[^>]+>", "", str(raw_title)).strip()
                    record["preview_title"] = clean_title[:120] if clean_title else "(untitled)"

                    record["preview_url"] = (
                        extracted.get("canonical_url")
                        or extracted.get("registration_url")
                        or payload.get("page_url")
                        or payload.get("url")
                    )
                    record["preview_community"] = (
                        extracted.get("community_name")
                        or extracted.get("organizer_name")
                    )
                    record["preview_confidence"] = raw.extraction_confidence
            except Exception:
                pass  # don't block the list on a single bad record

        enriched.append(record)

    return enriched


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


async def get_bad_date_events(db: AsyncSession) -> tuple[list[Event], int]:
    """Return events with junk placeholder dates (start_at > year 2050)."""
    from datetime import timezone
    sentinel = datetime(year=2050, month=1, day=1, tzinfo=timezone.utc)
    res = await db.execute(select(Event).where(Event.start_at > sentinel))
    items = res.scalars().all()
    return list(items), len(items)


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


# ─── Event Pipeline Queue ─────────────────────────────────────────────────────

_QUEUE_STATUSES = ("pending", "normalized", "moderation")


async def list_event_queue(db: AsyncSession, limit: int = 50) -> list[dict]:
    """Return raw_events currently in the pipeline queue (not yet published/rejected)."""
    import re as _re
    res = await db.execute(
        select(RawEvent)
        .where(RawEvent.pipeline_status.in_(_QUEUE_STATUSES))
        .order_by(RawEvent.seen_at.desc())
        .limit(limit)
    )
    items = res.scalars().all()
    result = []
    for raw in items:
        extracted = raw.ai_extracted_json or {}
        payload = raw.raw_payload_json or {}
        raw_title = extracted.get("title") or payload.get("title") or "(untitled)"
        clean_title = _re.sub(r"<[^>]+>", "", str(raw_title)).strip()
        result.append({
            "id": str(raw.id),
            "pipeline_status": raw.pipeline_status,
            "extraction_method": raw.extraction_method,
            "extraction_confidence": raw.extraction_confidence,
            "title": clean_title[:120] if clean_title else "(untitled)",
            "url": (
                extracted.get("canonical_url")
                or payload.get("page_url")
                or payload.get("url")
            ),
            "community": extracted.get("community_name") or extracted.get("organizer_name"),
            "reason": None,  # filled for moderation items below
            "seen_at": raw.seen_at,
        })
    # Enrich moderation items with reason
    mod_ids = [r["id"] for r in result if r["pipeline_status"] == "moderation"]
    if mod_ids:
        mod_res = await db.execute(
            select(ModerationQueueItem)
            .where(
                (ModerationQueueItem.entity_type == "raw_event")
                & (ModerationQueueItem.entity_id.in_(mod_ids))
            )
        )
        mod_map = {str(m.entity_id): m.reason for m in mod_res.scalars().all()}
        for r in result:
            if r["pipeline_status"] == "moderation":
                r["reason"] = mod_map.get(r["id"])
    return result


async def get_last_published_event(db: AsyncSession) -> dict | None:
    """Return the most recently published event."""
    res = await db.execute(
        select(Event)
        .where(Event.published_at.is_not(None))
        .order_by(Event.published_at.desc())
        .limit(1)
    )
    ev = res.scalar_one_or_none()
    if ev is None:
        return None
    return {
        "id": str(ev.id),
        "title": ev.title,
        "start_at": ev.start_at,
        "mode": ev.mode,
        "community_name": ev.community_name,
        "canonical_url": ev.canonical_url,
        "poster_url": ev.poster_url,
        "published_at": ev.published_at,
        "date_tba": ev.date_tba,
    }


async def remove_from_queue(db: AsyncSession, raw_event_id: str) -> bool:
    """Hard-delete a raw_event from the pipeline queue (admin action)."""
    raw = await db.get(RawEvent, raw_event_id)
    if raw is None:
        return False
    # Also clean up any moderation queue entries for this raw event
    mod_res = await db.execute(
        select(ModerationQueueItem).where(
            (ModerationQueueItem.entity_type == "raw_event")
            & (ModerationQueueItem.entity_id == raw_event_id)
        )
    )
    for mod in mod_res.scalars().all():
        await db.delete(mod)
    await db.delete(raw)
    await db.commit()
    return True


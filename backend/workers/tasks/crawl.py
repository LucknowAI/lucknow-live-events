from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from celery import shared_task
from sqlalchemy import delete, select, update

from api.core.database import SessionLocal
from api.models.event import Event
from api.models.source import Source
from workers.utils import run_async


log = structlog.get_logger(__name__)

# Events with start_at beyond this year are considered junk-date sentinels
_JUNK_DATE_YEAR = 2050


@shared_task(name="workers.tasks.crawl.crawl_all_sources")
def crawl_all_sources() -> dict:
    """Enqueue a pipeline task for every enabled, non-blacklisted source that is due to crawl."""

    async def _run() -> list[str]:
        async with SessionLocal() as db:
            now = datetime.now(timezone.utc)
            res = await db.execute(
                select(Source).where(
                    (Source.enabled == True) &  # noqa: E712
                    (Source.status != "blacklisted")
                )
            )
            sources = res.scalars().all()

            due: list[str] = []
            for s in sources:
                interval = int(s.crawl_interval_hours or 1)
                if s.last_crawled_at is None:
                    due.append(str(s.id))
                    continue
                if s.last_crawled_at + timedelta(hours=interval) <= now:
                    due.append(str(s.id))

            return due

    source_ids = run_async(_run())
    log.info("crawl_all_sources.dispatching", count=len(source_ids))

    from workers.tasks.pipeline import run_pipeline_for_source

    for sid in source_ids:
        run_pipeline_for_source.delay(sid)

    return {"ok": True, "dispatched": len(source_ids)}


@shared_task(name="workers.tasks.crawl.expire_past_events")
def expire_past_events() -> dict:
    """Three-pass cleanup:
    Pass A: Expire events with explicit end_at older than 48h.
    Pass B: Expire events with no end_at but start_at older than 7 days.
    Pass C: Hard-delete junk-date events (start_at > 2050) older than 1 day.
    """
    now = datetime.now(timezone.utc)
    results: dict[str, int] = {"pass_a": 0, "pass_b": 0, "pass_c_deleted": 0}

    async def _run() -> dict:
        async with SessionLocal() as db:
            # Pass A: has end_at and it's > 48h in the past
            cutoff_48h = now - timedelta(hours=48)
            stmt_a = (
                update(Event)
                .where(
                    (Event.expires_at.is_(None))
                    & (Event.end_at.is_not(None))
                    & (Event.end_at < cutoff_48h)
                )
                .values(expires_at=now)
            )
            res_a = await db.execute(stmt_a)
            results["pass_a"] = int(res_a.rowcount or 0)

            # Pass B: no end_at, but start_at was > 7 days ago (event definitely over)
            cutoff_7d = now - timedelta(days=7)
            stmt_b = (
                update(Event)
                .where(
                    (Event.expires_at.is_(None))
                    & (Event.end_at.is_(None))
                    & (Event.start_at < cutoff_7d)
                )
                .values(expires_at=now)
            )
            res_b = await db.execute(stmt_b)
            results["pass_b"] = int(res_b.rowcount or 0)

            # Pass C: hard-delete junk-date events (sentinel 2099/2050+)
            # Grace period: only delete if created > 24h ago (avoids mid-pipeline events)
            junk_sentinel = datetime(year=_JUNK_DATE_YEAR, month=1, day=1, tzinfo=timezone.utc)
            grace_cutoff = now - timedelta(hours=24)
            stmt_c = delete(Event).where(
                (Event.start_at > junk_sentinel)
                & (Event.created_at < grace_cutoff)
            )
            res_c = await db.execute(stmt_c)
            results["pass_c_deleted"] = int(res_c.rowcount or 0)

            await db.commit()
            return results

    final = run_async(_run())
    log.info("expire_past_events.done", **final)
    return {"ok": True, **final}


@shared_task(
    bind=True,
    name="workers.tasks.crawl.rescrape_single_event",
    max_retries=1,
    default_retry_delay=30,
)
def rescrape_single_event(self, event_id: str) -> dict:
    """Re-extract a single event directly from its canonical_url using Gemini Search Grounding.

    Unlike source re-crawl (which re-scrapes the whole source listing), this task:
    1. Loads the event's canonical_url from DB
    2. Fetches the page via httpx
    3. Runs extraction_agent (which will use Search Grounding for JS-heavy pages)
    4. Runs classification_agent
    5. Updates the event record in-place with fresh data
    """
    return run_async(_async_rescrape_single(event_id))


async def _async_rescrape_single(event_id: str) -> dict:
    from ai.classification_agent import ClassificationInput, classify_event
    from ai.extraction_agent import ExtractionInput, extract_event

    async with SessionLocal() as db:
        ev = await db.get(Event, event_id)
        if ev is None:
            return {"error": "event_not_found", "event_id": event_id}

        url = ev.canonical_url
        platform = ev.source_platform or "unknown"

        # Fetch the page content
        page_text = ""
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; LucknowEventsBot/1.0)"})
                page_text = resp.text[:8000]
        except Exception as fetch_err:
            log.warning("rescrape_single.fetch_failed", event_id=event_id, url=url, error=str(fetch_err))
            # Still attempt grounded extraction even without page text

        extraction = await extract_event(ExtractionInput(
            source_platform=platform,
            source_url=url,
            page_url=url,
            cleaned_text=page_text,
            partial_hints={"title": ev.title, "community": ev.community_name},
        ))

        if extraction.not_an_event or extraction.confidence < 0.3:
            log.info("rescrape_single.low_confidence", event_id=event_id, confidence=extraction.confidence)
            return {"skipped": True, "reason": "low_confidence", "event_id": event_id}

        # Update fields that the extraction found
        updated_fields: dict[str, object] = {}

        if extraction.start_at:
            from ingestion.normalizers.date import parse_datetime
            parsed = parse_datetime(extraction.start_at)
            if parsed and parsed.year < _JUNK_DATE_YEAR:
                ev.start_at = parsed
                updated_fields["start_at"] = str(parsed)

        if extraction.end_at:
            from ingestion.normalizers.date import parse_datetime
            parsed_end = parse_datetime(extraction.end_at)
            if parsed_end:
                ev.end_at = parsed_end
                updated_fields["end_at"] = str(parsed_end)

        if extraction.title and len(extraction.title) > 5:
            ev.title = extraction.title
            updated_fields["title"] = extraction.title

        if extraction.description:
            ev.description = extraction.description
            updated_fields["description"] = True

        if extraction.venue_name:
            ev.venue_name = extraction.venue_name
            updated_fields["venue_name"] = extraction.venue_name

        if extraction.locality:
            ev.locality = extraction.locality

        if extraction.registration_url:
            ev.registration_url = extraction.registration_url

        if extraction.mode:
            ev.mode = extraction.mode

        # Run classification to refresh topics/event_type
        classification = await classify_event(ClassificationInput(
            title=ev.title,
            description=ev.description,
            organizer_name=ev.organizer_name,
            community_name=ev.community_name,
            source_platform=platform,
            mode=ev.mode,
        ))

        if classification.event_type:
            ev.event_type = classification.event_type
        if classification.topics:
            ev.topics_json = classification.topics

        ev.updated_at = datetime.now(timezone.utc)
        await db.commit()

        log.info("rescrape_single.done", event_id=event_id, updated_fields=list(updated_fields.keys()))
        return {"ok": True, "event_id": event_id, "updated": list(updated_fields.keys())}


@shared_task(
    bind=True,
    name="workers.tasks.crawl.rescrape_bad_dates",
    max_retries=0,
)
def rescrape_bad_dates(self) -> dict:
    """Find all events with junk placeholder dates (start_at > 2050) and re-extract them.

    Events that still have no real date after re-extraction are deleted.
    """
    return run_async(_async_rescrape_bad_dates())


async def _async_rescrape_bad_dates() -> dict:
    async with SessionLocal() as db:
        junk_sentinel = datetime(year=_JUNK_DATE_YEAR, month=1, day=1, tzinfo=timezone.utc)
        res = await db.execute(
            select(Event).where(Event.start_at > junk_sentinel)
        )
        bad_events = res.scalars().all()

    log.info("rescrape_bad_dates.found", count=len(bad_events))

    results = {"total": len(bad_events), "fixed": 0, "deleted": 0, "errors": 0}

    # Run re-extractions concurrently (max 3 at once to stay within API rate limits)
    semaphore = asyncio.Semaphore(3)

    async def handle_one(ev_id: str) -> str:
        async with semaphore:
            try:
                result = await _async_rescrape_single(ev_id)
                if result.get("ok") and result.get("updated"):
                    # Check if start_at was actually fixed
                    async with SessionLocal() as db2:
                        ev = await db2.get(Event, ev_id)
                        if ev and ev.start_at.year < _JUNK_DATE_YEAR:
                            return "fixed"
                        elif ev:
                            # Still junk — delete it
                            await db2.delete(ev)
                            await db2.commit()
                            return "deleted"
                return "skipped"
            except Exception as e:
                log.warning("rescrape_bad_dates.event_error", event_id=ev_id, error=str(e))
                return "error"

    statuses = await asyncio.gather(*(handle_one(ev.id) for ev in bad_events))
    results["fixed"] = statuses.count("fixed")
    results["deleted"] = statuses.count("deleted")
    results["errors"] = statuses.count("error")

    log.info("rescrape_bad_dates.done", **results)
    return {"ok": True, **results}

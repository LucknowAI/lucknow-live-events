from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from celery import shared_task
from sqlalchemy import select

from api.core.database import SessionLocal
from api.models.event import Event
from api.models.source import Source
from workers.utils import run_async

log = structlog.get_logger(__name__)


@shared_task(name="workers.tasks.watchlist.refresh_watchlist_sources")
def refresh_watchlist_sources() -> dict[str, Any]:
    """
    Periodically re-scan previously-verified single-event URLs (watchlist sources)
    to keep details accurate (date/time changes, updated poster, updated description).

    This is intentionally conservative:
      - Only refreshes sources that have a currently-upcoming (or just-started) event.
      - Automatically disables watchlist once the event is in the past by >48h.
    """

    async def _pick_sources() -> tuple[list[str], int]:
        now = datetime.now(timezone.utc)
        recently_started_cutoff = now - timedelta(hours=12)
        expired_cutoff = now - timedelta(hours=48)

        async with SessionLocal() as db:
            # Watchlist sources are created by manual submissions (and AI discovery)
            # and marked with config_json.watchlist = true.
            watchlist_stmt = select(Source).where(Source.config_json["watchlist"].astext == "true")
            sources = (await db.execute(watchlist_stmt)).scalars().all()

            if not sources:
                return ([], 0)

            source_by_url = {s.base_url: s for s in sources if s.base_url}

            # Find matching upcoming events for those sources (canonical_url == base_url for generic sources).
            events_stmt = (
                select(Event)
                .where(
                    (Event.canonical_url.in_(list(source_by_url.keys())))
                    & (Event.published_at.is_not(None))
                    & (Event.expires_at.is_(None) | (Event.expires_at > now))
                )
                .order_by(Event.start_at.asc())
            )
            events = (await db.execute(events_stmt)).scalars().all()

            due_source_ids: list[str] = []
            disabled = 0

            for ev in events:
                src = source_by_url.get(ev.canonical_url)
                if not src:
                    continue

                # If the event is long past, stop refreshing this watchlist source.
                ended_at = ev.end_at or ev.start_at
                if isinstance(ended_at, datetime) and ended_at < expired_cutoff:
                    cfg = dict(src.config_json or {})
                    cfg["watchlist"] = False
                    cfg.pop("always_refresh", None)
                    src.config_json = cfg
                    disabled += 1
                    continue

                # Only refresh upcoming events (or ones that just started) to capture last-minute edits.
                if ev.start_at >= recently_started_cutoff:
                    # Force the ingestion to not skip unchanged snapshots for watchlist refresh runs.
                    cfg = dict(src.config_json or {})
                    cfg["always_refresh"] = True
                    src.config_json = cfg
                    due_source_ids.append(str(src.id))

            if disabled:
                await db.commit()

            return (due_source_ids, disabled)

    source_ids, disabled = run_async(_pick_sources())
    if not source_ids and not disabled:
        return {"ok": True, "refreshed": 0, "disabled": 0}

    from workers.tasks.pipeline import run_pipeline_for_source

    for sid in source_ids:
        run_pipeline_for_source.delay(sid)

    log.info("watchlist.refresh.dispatched", refreshed=len(source_ids), disabled=disabled)
    return {"ok": True, "refreshed": len(source_ids), "disabled": disabled}


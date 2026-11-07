"""Run every enabled source that has an adapter.

    python -m engine.run [--source PLATFORM] [--dry-run]

One source failing does not stop the others. Exit 0 only when every source
succeeded. Dry-run classifies new and changed events and writes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

import structlog
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.models.event import Event
from api.models.ingest import IngestRun
from api.models.source import Source
from engine import cleanup, delta, http, rules, store
from engine.sources import REGISTRY
from engine.sources.base import Rejection

log = structlog.get_logger(__name__)


def _blank_counts() -> dict[str, int]:
    return {"new": 0, "updated": 0, "unchanged": 0}


async def run(
    platform: str | None = None,
    dry_run: bool = False,
    *,
    session: AsyncSession | None = None,
    ping: bool = False,
) -> int:
    if session is None:
        # Imported here so `--help` does not need a database URL.
        from api.core.database import SessionLocal

        async with SessionLocal() as owned:
            return await _run(owned, platform, dry_run, ping)
    return await _run(session, platform, dry_run, ping)


async def _run(session: AsyncSession, platform: str | None, dry_run: bool, ping: bool) -> int:
    failed = False
    try:
        if platform is None:
            platforms = list(REGISTRY)
        elif platform in REGISTRY:
            platforms = [platform]
        else:
            platforms = []
        sources = []
        if platforms:
            sources = list(
                await session.scalars(
                    select(Source)
                    .where(Source.enabled.is_(True), Source.platform.in_(platforms))
                    .order_by(Source.name)
                )
            )
        if platform and not sources:
            log.info("engine.source", platform=platform, status="failed", error="no enabled source")
            return 1
        # Re-load each source by id. A failed source rolls the session back, which
        # expires the other objects, and a lazy read then fails on async.
        for source_id in [source.id for source in sources]:
            source = await session.get(Source, source_id)
            if source is None:
                continue
            if await _source(session, source, dry_run) != "success":
                failed = True
        if not dry_run:
            try:
                await cleanup.apply(session)
            except Exception:  # saved events must stay if cleanup fails
                await session.rollback()
                log.exception("engine.cleanup")
                failed = True
        if ping and not dry_run:
            await _ping(failed)
        return 1 if failed else 0
    finally:
        await http.close()


async def _source(session: AsyncSession, source: Source, dry_run: bool) -> str:
    name, platform = source.name, source.platform
    adapter = REGISTRY[platform]
    config = source.config_json or {}
    run_id = None
    try:
        if not dry_run:
            # DB default is not loaded on the object, and store.save reads started_at.
            run = IngestRun(source_id=source.id, started_at=datetime.now(UTC), status="running")
            session.add(run)
            await session.commit()
            run_id = run.id
        listing = await adapter.list_events(config)
        if not listing and await _had_events(session, source.id):
            # A sudden empty list is a failed fetch. Saving nothing keeps last_seen
            # put, so a later cleanup does not treat the old events as gone.
            await _finish(
                session,
                run_id=run_id,
                source=source,
                name=name,
                platform=platform,
                dry_run=dry_run,
                status="failed",
                error="empty listing",
            )
            return "failed"
        events, listed, rejected, reasons = await _collect(adapter, platform, config, listing)
        if dry_run:
            counts = await _preview(session, source, events)
        elif events or listed:
            counts = await store.save(
                session, source, await session.get(IngestRun, run_id), events, listed
            )
        else:
            counts = _blank_counts()
        await _finish(
            session,
            run_id=run_id,
            source=source,
            name=name,
            platform=platform,
            dry_run=dry_run,
            status="success",
            fetched=len(listing),
            rejected=rejected,
            reasons=reasons,
            counts=counts,
        )
        return "success"
    except Exception as exc:  # noqa: BLE001  # one bad source must not stop the run
        await session.rollback()
        await _finish(
            session,
            run_id=run_id,
            source=None,
            name=name,
            platform=platform,
            dry_run=dry_run,
            status="failed",
            error=f"{type(exc).__name__}: {exc}"[:500],
            exc=exc,
        )
        return "failed"


async def _collect(adapter, platform: str, config: dict, listing: list) -> tuple:
    events, listed = [], []
    rejected = 0
    reasons: dict[str, int] = {}

    def bump(reason: str) -> None:
        nonlocal rejected
        rejected += 1
        reasons[reason] = reasons.get(reason, 0) + 1

    for item in listing:
        try:
            external_id = adapter.external_id(item)
            if not isinstance(external_id, str) or not external_id.strip():
                raise ValueError("blank external_id")
        except Exception:  # noqa: BLE001  # a bad listing row is a reject, not a failed source
            bump("invalid")
            continue
        listed.append(external_id)
        try:
            raw = await adapter.fetch_detail(item)
        except Exception:  # noqa: BLE001  # detail failure still counts the id as seen
            bump("detail_failed")
            continue
        try:
            mapped = adapter.to_event(raw, config)
        except ValidationError:
            bump("invalid")
            continue
        if isinstance(mapped, Rejection):
            bump(mapped.reason)
            continue
        if mapped.source_platform != platform:
            bump("platform_mismatch")
            continue
        ok, reason = rules.check(mapped, config)
        if not ok:
            bump(reason or "invalid")
            continue
        events.append(mapped)
    return events, listed, rejected, reasons


async def _had_events(session: AsyncSession, source_id) -> bool:
    fetched = await session.scalar(
        select(IngestRun.fetched)
        .where(IngestRun.source_id == source_id, IngestRun.status == "success")
        .order_by(IngestRun.started_at.desc())
        .limit(1)
    )
    return bool(fetched)


async def _preview(session: AsyncSession, source: Source, events: list) -> dict[str, int]:
    counts = _blank_counts()
    if not events:
        return counts
    rows = await session.scalars(
        select(Event).where(
            Event.source_platform == source.platform,
            Event.external_id.in_([event.external_id for event in events]),
        )
    )
    hashes = {row.external_id: row.content_hash for row in rows}
    for event in events:
        kind = delta.classify(event, hashes.get(event.external_id))
        hashes[event.external_id] = delta.content_hash(event)
        if kind == "new":
            counts["new"] += 1
        elif kind == "changed":
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
            continue
        log.info(
            "engine.dry_run",
            source=source.name,
            external_id=event.external_id,
            change="new" if kind == "new" else "changed",
        )
    return counts


async def _finish(
    session: AsyncSession,
    *,
    run_id,
    source: Source | None,
    name: str,
    platform: str,
    dry_run: bool,
    status: str,
    fetched: int = 0,
    rejected: int = 0,
    reasons: dict[str, int] | None = None,
    counts: dict[str, int] | None = None,
    error: str | None = None,
    exc: BaseException | None = None,
) -> None:
    counts = counts or _blank_counts()
    # A dry-run writes no ingest_runs row. A success row without a last_seen
    # update would look like a miss to the cleanup job.
    if run_id is not None:
        run = await session.get(IngestRun, run_id)
        finished = datetime.now(UTC)
        run.finished_at = finished
        run.status = status
        run.fetched = fetched
        run.new = counts["new"]
        run.updated = counts["updated"]
        run.unchanged = counts["unchanged"]
        run.rejected = rejected
        run.reject_reasons = reasons or {}
        run.error = error
        if status == "success" and source is not None:
            source.last_success_at = finished
        await session.commit()
    log.info(
        "engine.source",
        source=name,
        platform=platform,
        status=status,
        fetched=fetched,
        new=counts["new"],
        updated=counts["updated"],
        unchanged=counts["unchanged"],
        rejected=rejected,
        dry_run=dry_run,
        error=error,
        exc_info=exc,
    )


async def _ping(failed: bool) -> None:
    url = os.environ.get("HEALTHCHECK_URL", "").strip()
    if not url:
        return
    target = f"{url.rstrip('/')}/fail" if failed else url
    try:
        response = await http.get(target)
        await response.aclose()
    except Exception:  # a missed ping must not change the run's exit code
        log.exception("engine.healthcheck")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m engine.run")
    parser.add_argument("--source", metavar="PLATFORM", help="Only sources of this platform")
    parser.add_argument(
        "--dry-run", action="store_true", help="Fetch and classify, but do not save"
    )
    args = parser.parse_args(argv)
    return asyncio.run(run(args.source, args.dry_run, ping=True))


if __name__ == "__main__":
    sys.exit(main())

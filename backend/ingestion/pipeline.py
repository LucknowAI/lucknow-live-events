"""Main ingestion pipeline.

Steps (per requirements §8):
1.  Fetch via adapter
2.  Snapshot + SHA256 hash (skip if unchanged)
3.  Raw deterministic extract → insert RawEvent
4.  AI extraction (conditional on confidence thresholds)
5.  Normalize (date / location / text / url)
6.  Relevance score
7.  Deduplicate
8.  Publish decision
9.  Enqueue feed rebuild
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import SessionLocal
from api.models.crawl import CrawlRun
from api.models.event import Event
from api.models.moderation import ModerationQueueItem
from api.models.raw_event import RawEvent
from api.models.source import Source
from ingestion.dedup import find_duplicate
from ingestion.normalizers.date import parse_datetime
from ingestion.normalizers.location import normalize_city, normalize_locality
from ingestion.normalizers.text import (
    MAX_EXTRACTION_CHARS,
    clean_text,
    ensure_absolute_url,
)
from ingestion.publish_score import PublishInputs, compute_publish_score, field_completeness, publish_threshold
from ingestion.relevance import NormalizedEventData, compute_relevance
from ingestion.storage import content_hash, get_storage, snapshot_key


log = structlog.get_logger(__name__)

_SNAPSHOT_SCHEMA_VERSION = "v2-meta-jsonld"


def _json_safe(obj: Any) -> Any:
    """
    Ensure a structure is JSON-serializable for JSONB storage.
    This prevents in-place mutation later in the pipeline (e.g. datetimes) from
    breaking flush/commit when persisting snapshots like ai_extracted_json.
    """

    def _default(o: Any) -> str:
        if isinstance(o, datetime):
            return o.isoformat()
        return str(o)

    return json.loads(json.dumps(obj, default=_default))


async def run_source_pipeline(source_id: str) -> dict[str, int]:
    """Entry point called by Celery task. Returns counts dict."""
    async with SessionLocal() as db:
        source = await db.get(Source, source_id)
        if source is None:
            log.error("pipeline.source_not_found", source_id=source_id)
            return {"error": "source_not_found"}

        crawl_run = CrawlRun(
            id=str(uuid.uuid4()),
            source_id=source_id,
            started_at=datetime.now(timezone.utc),
            status="running",
        )
        db.add(crawl_run)
        await db.commit()

        counts = {"events_found": 0, "events_new": 0, "events_published": 0, "events_queued": 0}
        try:
            counts = await _process_source(db, source, crawl_run)
            crawl_run.status = "success"
        except Exception as exc:
            log.exception("pipeline.failed", source_id=source_id, error=str(exc))
            crawl_run.status = "failed"
            crawl_run.error_summary = str(exc)[:500]
            source.consecutive_failures = (source.consecutive_failures or 0) + 1
            source.last_failure_at = datetime.now(timezone.utc)
        finally:
            crawl_run.finished_at = datetime.now(timezone.utc)
            for k, v in counts.items():
                setattr(crawl_run, k, v)
            source.last_crawled_at = datetime.now(timezone.utc)
            if crawl_run.status == "success":
                source.last_success_at = source.last_crawled_at
                source.consecutive_failures = 0
            await db.commit()

        return counts


async def _process_source(db: AsyncSession, source: Source, crawl_run: CrawlRun) -> dict[str, int]:
    from ingestion.adapters.generic import GenericAdapter
    from ingestion.adapters.static import StaticAdapter

    adapter_map = {
        "generic": GenericAdapter(),
        "static": StaticAdapter(),
    }
    adapter = adapter_map.get(source.platform or "generic") or adapter_map["generic"]  # type: ignore[arg-type]

    source_dict = {
        "id": str(source.id),
        "base_url": source.base_url,
        "config_json": source.config_json or {},
    }
    pages = await adapter.fetch(source_dict)

    storage = get_storage()
    counts = {"events_found": 0, "events_new": 0, "events_published": 0, "events_queued": 0}

    for page in pages:
        raw_bytes = (
            json.dumps(page.html_or_json, ensure_ascii=False).encode()
            if isinstance(page.html_or_json, (dict, list))
            else str(page.html_or_json).encode()
        )
        url_hash = content_hash(page.url.encode())
        # Snapshot key includes a schema/version so changes in parsers can trigger
        # a one-time refresh across all sources without manual cleanup.
        snap_key = snapshot_key(str(source.id), f"{_SNAPSHOT_SCHEMA_VERSION}:{url_hash}")
        page_hash = content_hash(raw_bytes)

        # ── Step 2: snapshot + hash check ──────────────────────────────
        existing_snap = storage.get(snap_key + ".hash")
        always_refresh = bool((source.config_json or {}).get("always_refresh"))
        if (not always_refresh) and existing_snap and existing_snap.decode() == page_hash:
            log.debug("pipeline.skip_unchanged", url=page.url)
            continue
        storage.put(snap_key, raw_bytes)
        storage.put(snap_key + ".hash", page_hash.encode())

        raw_events = adapter.extract_raw_events(page)
        counts["events_found"] += len(raw_events)

        for raw in raw_events:
            result = await _process_raw_event(db, source, raw, adapter, page.url)
            counts["events_new"] += result.get("new", 0)
            counts["events_published"] += result.get("published", 0)
            counts["events_queued"] += result.get("queued", 0)

    return counts


async def _process_raw_event(
    db: AsyncSession,
    source: Source,
    raw: dict[str, Any],
    adapter: Any,
    source_url: str,
) -> dict[str, int]:
    from ingestion.adapters.base import BaseAdapter

    ext_id = adapter.get_external_id(raw) if hasattr(adapter, "get_external_id") else None

    # ── Step 3: insert or update RawEvent ─────────────────────────────────
    raw_event = None
    if ext_id:
        stmt = select(RawEvent).where(
            (RawEvent.source_id == str(source.id)) & (RawEvent.external_id == ext_id)
        )
        raw_event = (await db.execute(stmt)).scalar_one_or_none()

    if raw_event:
        raw_event.raw_payload_json = raw
        raw_event.pipeline_status = "pending"
    else:
        raw_event = RawEvent(
            id=str(uuid.uuid4()),
            source_id=str(source.id),
            external_id=ext_id,
            raw_payload_json=raw,
            extraction_method="deterministic",
            extraction_confidence=0.0,
            pipeline_status="pending",
        )
        db.add(raw_event)

    await db.flush()

    # ── Step 4: deterministic parse + conditional AI ──────────────────
    parsed, confidence = _deterministic_parse(raw, source.platform or "")
    raw_event.extraction_confidence = confidence

    # ── Step 4a: page-type guardrail (reduce false events) ─────────────
    # If this URL doesn't look like a single event detail page, don't let it
    # flow through to publishing.
    page_kind = _classify_generic_page(raw, source_url)
    if page_kind != "detail":
        await _queue_moderation(db, raw_event, f"url_{page_kind}")
        raw_event.pipeline_status = "moderation"
        await db.commit()
        return {"new": 1, "queued": 1}

    _AI_EXTRACT_PLATFORMS = frozenset({"generic", "meetup", "commudle", "devfolio", "unstop"})
    needs_ai_extract = confidence < 0.60 or (
        (source.platform or "") in _AI_EXTRACT_PLATFORMS and bool(raw.get("_cleaned_text"))
    )

    if needs_ai_extract:
        parsed, confidence = await _ai_extract(raw_event, parsed, source.platform or "", source_url, confidence)
        raw_event.extraction_method = "ai"
        raw_event.extraction_confidence = confidence
        raw_event.ai_extracted_json = _json_safe(parsed)
    elif confidence < 0.85:
        parsed = await _ai_classify(parsed)
        raw_event.extraction_method = "hybrid"

    # ── Step 5: normalize ─────────────────────────────────────────────
    parsed["city"] = normalize_city(parsed.get("city"))
    parsed["locality"] = normalize_locality(parsed.get("locality"))
    parsed["start_at"] = parse_datetime(parsed.get("start_at"))
    parsed["end_at"] = parse_datetime(parsed.get("end_at"))
    # Keep description as None if empty after cleaning (avoid publishing blank strings).
    _desc_raw = parsed.get("description")
    if _desc_raw:
        _desc_clean = clean_text(str(_desc_raw))
        parsed["description"] = _desc_clean if _desc_clean.strip() else None
    else:
        parsed["description"] = None
    parsed["canonical_url"] = ensure_absolute_url(parsed.get("canonical_url") or source_url, source.base_url)
    parsed["registration_url"] = ensure_absolute_url(
        parsed.get("registration_url") or parsed.get("canonical_url") or source_url,
        source.base_url,
    )

    raw_event.pipeline_status = "normalized"
    await db.flush()

    # ── Step 6: relevance score ───────────────────────────────────────
    ev_data = NormalizedEventData(
        mode=parsed.get("mode"),
        city=parsed.get("city"),
        address=parsed.get("address"),
        venue_name=parsed.get("venue_name"),
        organizer_name=parsed.get("organizer_name"),
        community_name=parsed.get("community_name"),
    )
    relevance = compute_relevance(ev_data)

    if relevance < 0.3 and source.platform != "static":
        raw_event.pipeline_status = "rejected"
        await db.commit()
        return {}

    # ── Step 6b: reject junk titles produced by scraping noise ──────────
    if not _is_valid_title(parsed.get("title")):
        raw_event.pipeline_status = "rejected"
        log.debug("pipeline.junk_title_rejected", title=str(parsed.get("title", ""))[:120])
        await db.commit()
        return {}

    # ── Step 6c: handle missing start date ───────────────────────────
    # If no date is found: attempt a targeted grounded search for the date first.
    # If still nothing, publish as "Date TBA" if the event is otherwise valid
    # (real title, Lucknow-relevant, decent confidence). TBA events appear on
    # the events page (sorted to the bottom) but are excluded from the calendar.
    if parsed.get("start_at") is None:
        # Try grounded date lookup (second AI call, focused on date only)
        if confidence >= 0.30:
            try:
                from ai.extraction_agent import grounded_date_search
                date_result = await grounded_date_search(
                    page_url=parsed.get("canonical_url") or source_url,
                    known_title=parsed.get("title"),
                )
                if date_result.get("start_at"):
                    parsed["start_at"] = date_result["start_at"]
                    parsed["end_at"] = date_result.get("end_at") or parsed.get("end_at")
                    confidence = max(confidence, 0.65)
                    raw_event.extraction_confidence = confidence
                    log.info("pipeline.grounded_date_found", url=parsed.get("canonical_url"))
            except Exception as _exc:
                log.debug("pipeline.grounded_date_search_failed", error=str(_exc))

        if parsed.get("start_at") is None:
            # Still no date. Publish as TBA if event looks real enough.
            _tba_eligible = (
                confidence >= 0.45
                and relevance >= 0.50
                and _is_valid_title(parsed.get("title"))
            )
            if _tba_eligible:
                # Use a far-future sentinel date — expires_at = 30 days from now.
                from datetime import timezone
                parsed["start_at"] = datetime.now(timezone.utc).replace(
                    year=datetime.now(timezone.utc).year + 1
                )
                parsed["_date_tba"] = True
                log.info("pipeline.publishing_tba", title=parsed.get("title"))
            else:
                await _queue_moderation(db, raw_event, "missing_start_at")
                raw_event.pipeline_status = "moderation"
                await db.commit()
                return {"new": 1, "queued": 1}

    # ── Step 7: deduplicate ───────────────────────────────────────────
    dup = await find_duplicate(
        db,
        title=parsed.get("title"),
        start_at=parsed.get("start_at"),
        organizer=parsed.get("organizer_name") or parsed.get("community_name"),
        url=parsed.get("canonical_url"),
    )
    if dup is not None:
        # If we already have the event, attempt a safe metadata refresh (fix wrong dates,
        # missing poster, empty description) instead of dropping on the floor.
        updated = await _maybe_refresh_existing_event(db, dup, parsed)
        raw_event.pipeline_status = "duplicate"
        await db.commit()
        if updated:
            try:
                from workers.tasks.feeds import rebuild_all_feeds

                rebuild_all_feeds.delay()
            except Exception:
                pass
        log.debug("pipeline.duplicate_refresh", title=parsed.get("title"), updated=updated)
        return {"updated": 1} if updated else {}

    dedup_certainty = 1.0

    # ── Step 8: publish decision ──────────────────────────────────────
    completeness = field_completeness(parsed)
    score_inputs = PublishInputs(
        source_trust_score=source.trust_score,
        extraction_confidence=confidence,
        location_confidence=relevance,
        field_completeness=completeness,
        relevance_score=relevance,
        dedup_certainty=dedup_certainty,
    )
    publish_score = compute_publish_score(score_inputs)

    threshold = publish_threshold(source.trust_score or 0.7)
    if publish_score >= threshold:
        result = await _publish_event(db, raw_event, parsed, source, relevance, publish_score)
        raw_event.pipeline_status = "published"
        await db.commit()
        # ── Step 9: enqueue feed rebuild ─────────────────────────────
        try:
            from workers.tasks.feeds import rebuild_all_feeds
            rebuild_all_feeds.delay()
        except Exception:
            pass
        return {"new": 1, "published": 1}

    if threshold - 0.20 <= publish_score < threshold:
        # Re-run classification once and re-evaluate.
        parsed = await _ai_classify(parsed)
        completeness = field_completeness(parsed)
        score_inputs.field_completeness = completeness
        publish_score = compute_publish_score(score_inputs)
        if publish_score >= threshold:
            await _publish_event(db, raw_event, parsed, source, relevance, publish_score)
            raw_event.pipeline_status = "published"
            await db.commit()
            return {"new": 1, "published": 1}

    # Below threshold → moderation queue.
    await _queue_moderation(db, raw_event, "low_confidence")
    raw_event.pipeline_status = "moderation"
    await db.commit()
    return {"new": 1, "queued": 1}


_JUNK_TITLE_RE = __import__("re").compile(
    r"^(?:\{|\[|\.site-nav|\.nav-|function\s|\(function|\.site-|var |-webkit-|\*\s*\{)",
    __import__("re").IGNORECASE,
)

def _classify_generic_page(raw: dict[str, Any], url: str) -> str:
    """
    Heuristic classifier to block obvious non-event pages from being treated as a single event.

    Returns: "detail" | "listing" | "noise"
    """
    # 1) JSON-LD Event is the strongest detail-page signal.
    json_ld = raw.get("_json_ld")
    if isinstance(json_ld, str) and _json_ld_contains_event(json_ld):
        return "detail"

    # 2) URL patterns (fast + high precision)
    try:
        from urllib.parse import urlparse

        p = urlparse(url)
        host = (p.netloc or "").lower()
        path = (p.path or "").lower().rstrip("/")
    except Exception:
        host = ""
        path = (url or "").lower()

    # Known detail-ish patterns
    if "lu.ma" in host and path.count("/") == 1 and len(path) > 2:
        return "detail"
    if "gdg.community.dev" in host and "/events/details/" in path:
        return "detail"
    if "commudle.com" in host and "/events/" in path and "/communities/" in path:
        return "detail"
    if "unstop.com" in host and ("/hackathons/" in path or "/competitions/" in path):
        # Unstop has lots of listing pages too; prefer explicit slugs.
        return "detail" if path.count("/") >= 3 else "listing"
    if "meetup.com" in host and "/events/" in path:
        return "detail"

    # Known listing/noise patterns
    if "gdg.community.dev" in host and "/events/details/" not in path:
        return "listing"
    if "meetup.com" in host and "/events/" not in path:
        return "listing"
    if "community.cncf.io" in host:
        return "listing"
    if "commudle.com" in host and "/events/" not in path:
        return "listing"
    if "fossunited.org" in host and (path == "/c/lucknow" or path.startswith("/c/")):
        # Community chapter pages are listings; individual event pages use /c/<city>/<year> etc.
        return "listing" if path.count("/") <= 2 else "detail"

    # 3) fallback: if we have very little text, treat as noise
    cleaned = raw.get("_cleaned_text")
    if isinstance(cleaned, str) and len(cleaned.strip()) < 200:
        return "noise"

    # Default to detail to avoid blocking unknown platforms.
    return "detail"


def _json_ld_contains_event(json_ld: str) -> bool:
    import json

    def _has_event(obj: Any) -> bool:
        if isinstance(obj, dict):
            t = obj.get("@type")
            if t == "Event":
                return True
            if isinstance(t, list) and any(x == "Event" for x in t):
                return True
            graph = obj.get("@graph")
            if isinstance(graph, list) and any(_has_event(x) for x in graph):
                return True
            return any(_has_event(v) for v in obj.values())
        if isinstance(obj, list):
            return any(_has_event(x) for x in obj)
        return False

    # Generic adapter may concatenate multiple JSON-LD blocks with separators.
    parts = [p.strip() for p in str(json_ld).split("\n\n") if p.strip()]
    for part in parts[:6]:
        try:
            data = json.loads(part)
        except Exception:
            continue
        if _has_event(data):
            return True
    return False


def _is_valid_title(title: Any) -> bool:
    """Return False if title looks like scraped JS/CSS noise or a JSON-encoded dict."""
    if not title:
        return False
    s = str(title).strip()
    if len(s) < 4 or len(s) > 600:
        return False
    if s.lower() in {"meta description:", "page text:", "json-ld:", "poster/image url:"}:
        return False
    # Starts with { or [ → JSON dict/array leaked into title field
    if s.startswith('{') or s.startswith('['):
        return False
    # CSS/JS noise patterns
    if _JUNK_TITLE_RE.search(s):
        return False
    return True


def _deterministic_parse(raw: dict[str, Any], platform: str) -> tuple[dict[str, Any], float]:
    """Best-effort field extraction without AI. Returns (parsed_dict, confidence)."""
    parsed: dict[str, Any] = {}
    found = 0
    total = 5  # title, start_at, canonical_url, mode, description

    for title_key in ("title", "name", "event_title"):
        if raw.get(title_key):
            parsed["title"] = str(raw[title_key])
            found += 1
            break

    for url_key in ("url", "event_url", "link", "canonical_url", "absolute_url"):
        if raw.get(url_key):
            parsed["canonical_url"] = str(raw[url_key])
            found += 1
            break

    for start_key in ("start_at", "start_date", "starts_at", "datetime", "date", "start_time"):
        if raw.get(start_key):
            parsed["start_at"] = str(raw[start_key])
            found += 1
            break

    for end_key in ("end_at", "end_date", "ends_at", "end_time"):
        if raw.get(end_key):
            parsed["end_at"] = str(raw[end_key])
            break

    parsed["mode"] = raw.get("mode") or raw.get("event_mode") or raw.get("type")
    if parsed["mode"]:
        found += 0.5

    for desc_key in ("description", "summary", "body", "about"):
        if raw.get(desc_key):
            parsed["description"] = str(raw[desc_key])
            found += 0.5
            break

    parsed["short_description"] = raw.get("short_description") or raw.get("tagline")
    parsed["venue_name"] = raw.get("venue") or raw.get("venue_name") or raw.get("location")
    parsed["city"] = raw.get("city") or raw.get("location_city")
    parsed["locality"] = raw.get("locality") or raw.get("neighborhood")
    parsed["community_name"] = raw.get("community_name") or raw.get("group_name") or raw.get("organizer")
    parsed["organizer_name"] = raw.get("organizer_name") or raw.get("host")
    parsed["registration_url"] = raw.get("registration_url") or raw.get("rsvp_url")
    parsed["poster_url"] = raw.get("poster_url") or raw.get("image") or raw.get("banner_url")
    parsed["is_free"] = raw.get("is_free", True)
    parsed["topics"] = raw.get("topics") or raw.get("tags") or []
    parsed["event_type"] = raw.get("event_type") or raw.get("type")
    parsed["source_platform"] = platform

    confidence = min(1.0, found / total)
    return parsed, confidence


async def _ai_extract(
    raw_event: RawEvent,
    partial: dict[str, Any],
    platform: str,
    source_url: str,
    current_confidence: float,
) -> tuple[dict[str, Any], float]:
    """Call Gemini extraction agent; on any failure return the partial parsed dict."""
    try:
        from ai.extraction_agent import ExtractionInput, extract_event

        raw = raw_event.raw_payload_json or {}
        # Prefer the pre-cleaned text from the adapter (e.g. GenericAdapter / GDG fallback)
        # rather than JSON-encoding the whole raw dict, which sends JS/CSS boilerplate
        # to Gemini and causes it to return junk titles.
        if isinstance(raw, dict) and raw.get("_cleaned_text"):
            text = str(raw["_cleaned_text"])[:MAX_EXTRACTION_CHARS]
        else:
            text = clean_text(
                json.dumps(raw, ensure_ascii=False),
                max_chars=MAX_EXTRACTION_CHARS,
            )

        inp = ExtractionInput(
            source_platform=platform,
            source_url=source_url,
            page_url=partial.get("canonical_url") or source_url,
            cleaned_text=text,
            partial_hints=partial,
        )
        result = await extract_event(inp)
        if result.not_an_event:
            log.debug("pipeline.ai_not_an_event", url=partial.get("canonical_url"))
            return partial, 0.0

        merged = dict(partial)
        merged.update({k: v for k, v in result.model_dump().items() if v is not None})
        return merged, result.confidence
    except Exception as exc:
        log.error("pipeline.ai_extract_failed", error=str(exc))
        return partial, current_confidence


async def _ai_classify(parsed: dict[str, Any]) -> dict[str, Any]:
    try:
        from ai.classification_agent import ClassificationInput, classify_event

        inp = ClassificationInput(
            title=parsed.get("title") or "",
            description=parsed.get("description"),
            organizer_name=parsed.get("organizer_name"),
            community_name=parsed.get("community_name"),
            source_platform=parsed.get("source_platform") or "",
            mode=parsed.get("mode"),
        )
        result = await classify_event(inp)
        updated = dict(parsed)
        if result.event_type:
            updated["event_type"] = result.event_type
        if result.topics:
            updated["topics"] = result.topics
        if result.audience:
            updated["audience"] = result.audience
        updated["is_student_friendly"] = result.is_student_friendly
        return updated
    except Exception as exc:
        log.error("pipeline.ai_classify_failed", error=str(exc))
        return parsed


async def _publish_event(
    db: AsyncSession,
    raw_event: RawEvent,
    parsed: dict[str, Any],
    source: Source,
    relevance_score: float,
    publish_score: float,
) -> Event:
    title = parsed.get("title") or "Untitled Event"
    base_slug = slugify(title, max_length=280)
    slug = base_slug
    # Ensure slug uniqueness.
    counter = 1
    while (await db.execute(select(Event).where(Event.slug == slug))).scalar_one_or_none():
        slug = f"{base_slug}-{counter}"
        counter += 1

    start_at = parsed.get("start_at")
    end_at = parsed.get("end_at")
    is_date_tba = bool(parsed.get("_date_tba", False))

    if not isinstance(start_at, datetime):
        raise ValueError("Refusing to publish event without start_at")

    if is_date_tba:
        # TBA events expire 30 days from now if no real date is ever scraped.
        expires_at = datetime.now(timezone.utc) + timedelta(days=30)
    elif isinstance(end_at, datetime):
        expires_at = end_at + timedelta(hours=48)
    else:
        expires_at = None

    event = Event(
        id=str(uuid.uuid4()),
        slug=slug,
        title=title,
        description=parsed.get("description"),
        short_description=(
            parsed.get("short_description")
            or ((parsed.get("description") or "")[:500] if parsed.get("description") else None)
        ),
        start_at=start_at,
        end_at=end_at,
        city=parsed.get("city") or "Lucknow",
        locality=parsed.get("locality"),
        venue_name=parsed.get("venue_name"),
        address=parsed.get("address"),
        mode=parsed.get("mode"),
        event_type=parsed.get("event_type"),
        topics_json=parsed.get("topics") or [],
        audience_json=parsed.get("audience") or [],
        organizer_name=parsed.get("organizer_name"),
        community_name=parsed.get("community_name"),
        source_platform=source.platform,
        canonical_url=parsed["canonical_url"],
        registration_url=parsed.get("registration_url") or parsed["canonical_url"],
        poster_url=parsed.get("poster_url"),
        is_free=bool(parsed.get("is_free", True)),
        is_student_friendly=bool(parsed.get("is_student_friendly", False)),
        date_tba=is_date_tba,
        relevance_score=relevance_score,
        publish_score=publish_score,
        raw_event_id=str(raw_event.id),
        published_at=datetime.now(timezone.utc),
        expires_at=expires_at,
    )
    db.add(event)
    await db.flush()
    return event


async def _maybe_refresh_existing_event(db: AsyncSession, event: Event, parsed: dict[str, Any]) -> bool:
    """
    Update an existing Event only when the incoming data is clearly better.
    This is primarily to correct cases where:
      - start_at was missing and we fell back to "now"
      - poster_url / description were empty at publish time
    """
    changed = False

    new_start = parsed.get("start_at")
    if isinstance(new_start, datetime):
        # If start_at looks like it was defaulted at publish time, it will be very close to published_at.
        looks_defaulted = False
        if event.published_at is not None:
            try:
                looks_defaulted = abs((event.start_at - event.published_at).total_seconds()) < 120
            except Exception:
                looks_defaulted = False
        # Also treat far-future sentinel dates as defaulted (historical bug).
        if event.start_at and getattr(event.start_at, "year", 0) >= 2090:
            looks_defaulted = True

        # Only overwrite if existing looks defaulted OR if new start differs materially (>= 6h).
        materially_different = abs((event.start_at - new_start).total_seconds()) >= 6 * 3600
        if (looks_defaulted or materially_different) and new_start != event.start_at:
            event.start_at = new_start
            changed = True

    new_end = parsed.get("end_at")
    if isinstance(new_end, datetime) and (event.end_at is None or new_end != event.end_at):
        event.end_at = new_end
        changed = True

    if (not event.description) and parsed.get("description"):
        event.description = parsed.get("description")
        changed = True

    if (not event.short_description) and parsed.get("short_description"):
        event.short_description = parsed.get("short_description")
        changed = True

    if (not event.poster_url) and parsed.get("poster_url"):
        event.poster_url = parsed.get("poster_url")
        changed = True

    if (not event.venue_name) and parsed.get("venue_name"):
        event.venue_name = parsed.get("venue_name")
        changed = True

    if (not event.locality) and parsed.get("locality"):
        event.locality = parsed.get("locality")
        changed = True

    if changed:
        await db.flush()
    return changed


async def _queue_moderation(db: AsyncSession, raw_event: RawEvent, reason: str) -> None:
    item = ModerationQueueItem(
        id=str(uuid.uuid4()),
        entity_type="raw_event",
        entity_id=str(raw_event.id),
        reason=reason,
        severity="low",
        status="pending",
    )
    db.add(item)
    await db.flush()

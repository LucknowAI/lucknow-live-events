"""Celery task: process a user-submitted event URL.

Flow:
1. Validate the URL is reachable and likely contains a real tech event (AI gate).
2. Create a temporary Source for the URL.
3. Run the ingestion pipeline.
4. If ≥1 event was published:
   - Keep the Source enabled → it will be crawled hourly.
   - Return success.
5. If AI validation fails or no events extracted:
   - Disable / delete the source.
   - Return moderation result.
"""
from __future__ import annotations

import asyncio
import structlog
from typing import Any

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import SessionLocal
from api.models.source import Source

log = structlog.get_logger(__name__)


@shared_task(
    bind=True,
    name="workers.tasks.submissions.process_manual_submission",
    max_retries=0,
    acks_late=True,
)
def process_manual_submission(
    self,
    submission_id: str,
    event_url: str,
    submitter_name: str | None = None,
    submitter_email: str | None = None,
) -> dict[str, Any]:
    return asyncio.get_event_loop().run_until_complete(
        _async_process(submission_id, event_url, submitter_name, submitter_email)
    )


async def _async_process(
    submission_id: str,
    event_url: str,
    submitter_name: str | None,
    submitter_email: str | None,
) -> dict[str, Any]:
    from ingestion.pipeline import run_source_pipeline

    async with SessionLocal() as db:
        # ── Step 1: AI gate – is this actually a tech event? ─────────────────
        is_valid = await _ai_validate_event_url(event_url)
        if not is_valid["ok"]:
            log.info("submission.ai_gate_rejected", url=event_url, reason=is_valid["reason"])
            return {
                "status": "rejected",
                "reason": is_valid["reason"],
                "submission_id": submission_id,
            }

        # ── Step 2: Create a temporary Source ────────────────────────────────
        from urllib.parse import urlparse
        source = Source(
            name=f"Submission: {event_url[:100]}",
            platform="generic",
            base_url=event_url,
            enabled=True,
            crawl_strategy="generic",
            config_json={
                "submission_id": submission_id,
                "submitter_name": submitter_name or "anonymous",
                "max_items": 1,
                # Watchlist sources are periodically refreshed to keep details accurate
                # (date/time changes, updated poster, updated description).
                "watchlist": True,
            },
            crawl_interval_hours=24,
            trust_score=0.60,  # User submissions start with lower trust
        )
        db.add(source)
        await db.commit()
        await db.refresh(source)

        log.info("submission.source_created", source_id=source.id, url=event_url)

        # ── Step 3: Run the ingestion pipeline ───────────────────────────────
        try:
            result = await run_source_pipeline(str(source.id))
        except Exception as exc:
            log.exception("submission.pipeline_error", error=str(exc))
            source.enabled = False
            await db.commit()
            return {
                "status": "error",
                "reason": str(exc),
                "submission_id": submission_id,
                "source_id": source.id,
            }

        published = result.get("published", 0)

        # ── Step 4: Keep source enabled for future hourly crawls if event published ─
        if published >= 1:
            # Graduated trust: user submission that produced a real event earns 0.70
            source.trust_score = 0.70
            source.enabled = True
            await db.commit()
            log.info(
                "submission.accepted",
                source_id=source.id,
                published=published,
                url=event_url,
            )
            return {
                "status": "published",
                "published": published,
                "submission_id": submission_id,
                "source_id": source.id,
            }

        # ── Step 5: No events found – disable source ─────────────────────────
        source.enabled = False
        await db.commit()
        log.info("submission.no_events", url=event_url, result=result)
        return {
            "status": "no_events",
            "result": result,
            "submission_id": submission_id,
            "source_id": source.id,
        }


async def _ai_validate_event_url(url: str) -> dict[str, Any]:
    """
    Fast AI gate: fetch the page (lightweight httpx, no Playwright), ask Gemini
    whether it looks like a real upcoming tech event. Returns {"ok": bool, "reason": str}.
    Falls back to True (allow) when AI is unavailable to avoid blocking legitimate events.
    """
    from api.core.config import settings

    # Quick sanity: must be a real URL
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {"ok": False, "reason": "Invalid URL format"}

    if settings.AI_MODE.lower() == "mock":
        # In mock mode, accept everything (used during local dev)
        return {"ok": True, "reason": "mock_mode"}

    # Fetch a preview of the page
    page_text, status_code = await _lightweight_fetch(url)
    
    # ── Fast-Fail Optimization ───────────────────────────────────────────────
    if status_code in (404, 410, 500, 502, 503):
        return {"ok": False, "reason": f"Fast-Fail: URL returned HTTP {status_code}"}
        
    lower_text = page_text.lower()
    if "<title>404" in lower_text or "page not found" in lower_text or "link expired" in lower_text:
        return {"ok": False, "reason": "Fast-Fail: Page explicitly says Not Found or Expired in HTML."}
    
    if not page_text and status_code == 0:
        return {"ok": True, "reason": "unreachable_but_allowed"}

    # Ask Gemini
    try:
        from ai.gemini_client import get_client, json_config
        from pydantic import BaseModel

        class ValidationResult(BaseModel):
            is_tech_event: bool
            is_upcoming: bool  # True if the event date appears to be in the future
            reason: str
            confidence: float

        client = get_client()
        prompt = (
            "Analyze this page content and determine if it represents a single real, "
            "upcoming tech event (conference, meetup, workshop, hackathon) in Lucknow "
            "or nearby UP, India. Return JSON only.\n\n"
            f"Page URL: {url}\n\nPage preview (first 2000 chars):\n{page_text[:2000]}"
        )
        resp = await client.aio.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=prompt,
            config=json_config(ValidationResult, system_instruction=(
                "You validate whether a web page represents a real, upcoming tech event. "
                "Be conservative: if unsure, set is_tech_event=false."
            )),
        )
        parsed = getattr(resp, "parsed", None)
        result = (
            ValidationResult.model_validate(parsed)
            if parsed
            else ValidationResult.model_validate_json(resp.text)
        )
        if result.is_tech_event and result.confidence >= 0.5:
            return {"ok": True, "reason": result.reason}
        return {
            "ok": False,
            "reason": f"Not a valid tech event: {result.reason}",
        }
    except Exception as exc:
        log.warning("submission.ai_gate_error", error=str(exc))
        # Fail open: allow the submission to proceed when AI is unavailable
        return {"ok": True, "reason": "ai_unavailable_fail_open"}


async def _lightweight_fetch(url: str) -> tuple[str, int]:
    """Fetch a URL with httpx (no JS rendering) for the AI gate check."""
    import httpx

    try:
        async with httpx.AsyncClient(
            timeout=15,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            },
            follow_redirects=True,
        ) as client:
            r = await client.get(url)
            return r.text[:4000], r.status_code
    except Exception as exc:
        log.warning("submission.fetch_failed", url=url, error=str(exc))
        return "", 0

"""Admin-triggered discovery router.

Allows admin to:
- Manually trigger the auto-discovery agent
- Run discovery with custom search queries
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from api.core.deps import get_current_admin
from api.schemas.admin import DiscoveryRunRequest, DiscoveryRunResult


router = APIRouter()
Admin = Annotated[dict, Depends(get_current_admin)]


@router.post("/run", response_model=DiscoveryRunResult, status_code=202)
async def run_discovery(admin: Admin):
    """Trigger the full AI-powered event discovery agent (uses default search queries)."""
    from workers.tasks.discovery import auto_discover_events
    task = auto_discover_events.delay()
    return DiscoveryRunResult(task_id=task.id, message="Discovery agent queued with default queries")


@router.post("/run-custom", response_model=DiscoveryRunResult, status_code=202)
async def run_custom_discovery(payload: DiscoveryRunRequest, admin: Admin):
    """Trigger discovery with admin-supplied custom search queries."""
    from workers.tasks.discovery import auto_discover_events
    task = auto_discover_events.delay(custom_queries=payload.custom_queries)
    return DiscoveryRunResult(
        task_id=task.id,
        message=f"Discovery agent queued with {len(payload.custom_queries or [])} custom queries",
    )


@router.post("/submit-url", status_code=202)
async def admin_submit_url(event_url: str, admin: Admin):
    """Admin can directly submit a URL through the standard submission pipeline."""
    from api.core.database import SessionLocal
    from api.services.submission_service import create_submission

    async with SessionLocal() as db:
        submission = await create_submission(
            db,
            event_url=event_url,
            submitter_name="Admin (Direct Submit)",
            submitter_email="admin@nawab.ai",
            notes="Directly submitted via admin mission-control",
        )
    return {"status": "queued", "submission_id": str(submission.id), "url": event_url}

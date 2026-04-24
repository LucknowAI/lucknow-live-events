from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.core.deps import get_current_admin
from api.schemas.admin import ModerationItemOut
from api.services import admin_service


router = APIRouter()
Admin = Annotated[dict, Depends(get_current_admin)]


# ─── Legacy: raw_event moderation queue (read-only, AI-managed) ───────────────
# Kept for internal inspection but no human approve/reject needed.

@router.get("", response_model=list[ModerationItemOut])
async def list_pending(admin: Admin, db: AsyncSession = Depends(get_db)):
    """List raw_event items in the AI pipeline queue (for inspection only)."""
    return await admin_service.list_pending_moderation(db)


# ─── Community Submissions ────────────────────────────────────────────────────

class CommunitySubmissionOut(BaseModel):
    id: str
    community_name: str | None
    community_url: str | None
    community_description: str | None
    submitter_name: str | None
    submitter_email: str | None
    notes: str | None
    status: str
    created_at: Any  # datetime


class CommunitySubmissionCreate(BaseModel):
    community_name: str
    community_url: str
    community_description: str | None = None
    submitter_name: str | None = None
    submitter_email: str | None = None
    notes: str | None = None


@router.get("/communities", response_model=list[CommunitySubmissionOut])
async def list_community_submissions(
    admin: Admin,
    status: str = "pending",
    db: AsyncSession = Depends(get_db),
):
    """List community link submissions pending admin review."""
    return await admin_service.list_community_submissions(db, status=status)


@router.post("/communities", response_model=dict, status_code=201)
async def create_community_submission(
    body: CommunitySubmissionCreate,
    admin: Admin,
    db: AsyncSession = Depends(get_db),
):
    """Admin: manually add a community submission for review."""
    return await admin_service.create_community_submission(db, body.model_dump())


@router.post("/communities/{submission_id}/approve", response_model=dict)
async def approve_community(
    submission_id: str,
    admin: Admin,
    db: AsyncSession = Depends(get_db),
):
    result = await admin_service.resolve_community_submission(db, submission_id, "approved")
    if result is None:
        raise HTTPException(status_code=404, detail="Community submission not found")
    return result


@router.post("/communities/{submission_id}/reject", response_model=dict)
async def reject_community(
    submission_id: str,
    admin: Admin,
    db: AsyncSession = Depends(get_db),
):
    result = await admin_service.resolve_community_submission(db, submission_id, "rejected")
    if result is None:
        raise HTTPException(status_code=404, detail="Community submission not found")
    return result

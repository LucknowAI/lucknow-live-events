from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.core.deps import get_current_admin
from api.schemas.admin import CrawlRunOut, SourceCreate, SourceOut, SourcePatch, SourceStatusUpdate
from api.services import admin_service


router = APIRouter()
Admin = Annotated[dict, Depends(get_current_admin)]


@router.get("", response_model=list[SourceOut])
async def list_sources(admin: Admin, db: AsyncSession = Depends(get_db)):
    return await admin_service.list_sources(db)


@router.post("", response_model=SourceOut, status_code=status.HTTP_201_CREATED)
async def create_source(payload: SourceCreate, admin: Admin, db: AsyncSession = Depends(get_db)):
    return await admin_service.create_source(db, payload.model_dump())


@router.patch("/{source_id}", response_model=SourceOut)
async def patch_source(source_id: str, payload: SourcePatch, admin: Admin, db: AsyncSession = Depends(get_db)):
    updated = await admin_service.patch_source(
        db, source_id, {k: v for k, v in payload.model_dump().items() if v is not None}
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return updated


@router.post("/{source_id}/status", response_model=SourceOut)
async def set_source_status(
    source_id: str,
    payload: SourceStatusUpdate,
    admin: Admin,
    db: AsyncSession = Depends(get_db),
):
    """Set source status: active | whitelisted | blacklisted."""
    if payload.status not in ("active", "whitelisted", "blacklisted"):
        raise HTTPException(status_code=422, detail="Status must be one of: active, whitelisted, blacklisted")
    updated = await admin_service.set_source_status(db, source_id, payload.status)
    if updated is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return updated


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(source_id: str, admin: Admin, db: AsyncSession = Depends(get_db)):
    deleted = await admin_service.delete_source(db, source_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Source not found")


@router.post("/crawl/run/{source_id}", status_code=202)
async def trigger_crawl(source_id: str, admin: Admin, db: AsyncSession = Depends(get_db)):
    src = await admin_service.get_source(db, source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="Source not found")
    from workers.tasks.pipeline import run_pipeline_for_source
    task = run_pipeline_for_source.delay(source_id)
    return {"task_id": task.id, "source_id": source_id}


@router.post("/crawl/run-all", status_code=202)
async def trigger_all_crawls(admin: Admin):
    from workers.tasks.crawl import crawl_all_sources
    task = crawl_all_sources.delay()
    return {"task_id": task.id}


@router.get("/crawl/runs", response_model=list[CrawlRunOut])
async def crawl_runs(admin: Admin, db: AsyncSession = Depends(get_db)):
    return await admin_service.list_crawl_runs(db)

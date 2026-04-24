from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.schemas.discovery import FacetItem, FacetsListResponse
from api.services import discovery_service

router = APIRouter()


@router.get("/topics", response_model=FacetsListResponse)
async def list_topics(
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    rows = await discovery_service.list_topics_with_counts(db, limit=limit)
    return FacetsListResponse(items=[FacetItem(name=n, count=c) for n, c in rows])


@router.get("/communities", response_model=FacetsListResponse)
async def list_communities(
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    rows = await discovery_service.list_communities_with_counts(db, limit=limit)
    return FacetsListResponse(items=[FacetItem(name=n, count=c) for n, c in rows])


@router.get("/localities", response_model=FacetsListResponse)
async def list_localities(
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    rows = await discovery_service.list_localities_with_counts(db, limit=limit)
    return FacetsListResponse(items=[FacetItem(name=n, count=c) for n, c in rows])

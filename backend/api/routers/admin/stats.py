from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.core.database import get_db
from api.core.deps import get_current_admin
from api.schemas.admin import StatsOut
from api.services import admin_service


router = APIRouter()
Admin = Annotated[dict, Depends(get_current_admin)]


@router.get("", response_model=StatsOut)
async def stats(admin: Admin, db: AsyncSession = Depends(get_db)):
    return await admin_service.get_stats(db)

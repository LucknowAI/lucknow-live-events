from fastapi import APIRouter

from api.routers.admin.auth import router as auth_router
from api.routers.admin.sources import router as sources_router
from api.routers.admin.moderation import router as moderation_router
from api.routers.admin.events import router as events_router
from api.routers.admin.stats import router as stats_router

router = APIRouter()
router.include_router(auth_router, prefix="/auth", tags=["admin-auth"])
router.include_router(sources_router, prefix="/sources", tags=["admin-sources"])
router.include_router(moderation_router, prefix="/moderation", tags=["admin-moderation"])
router.include_router(events_router, prefix="/events", tags=["admin-events"])
router.include_router(stats_router, prefix="/stats", tags=["admin-stats"])

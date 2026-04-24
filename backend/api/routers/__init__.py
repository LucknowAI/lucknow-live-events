from fastapi import APIRouter

from api.routers.admin import router as admin_router
from api.routers.discovery import router as discovery_router
from api.routers.events import router as events_router
from api.routers.feeds import router as feeds_router
from api.routers.submissions import router as submissions_router

router = APIRouter()
router.include_router(events_router, prefix="/events", tags=["events"])
router.include_router(feeds_router, prefix="/feeds", tags=["feeds"])
router.include_router(submissions_router, prefix="/submissions", tags=["submissions"])
router.include_router(discovery_router, tags=["discovery"])
router.include_router(admin_router, prefix="/admin", tags=["admin"])


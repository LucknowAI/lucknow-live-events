from api.services import admin_service
from api.services.event_service import (
    get_event_by_slug,
    list_events,
    list_featured,
    list_student_friendly,
    list_this_week,
)
from api.services.submission_service import create_submission

__all__ = [
    "admin_service",
    "list_events",
    "get_event_by_slug",
    "list_featured",
    "list_this_week",
    "list_student_friendly",
    "create_submission",
]


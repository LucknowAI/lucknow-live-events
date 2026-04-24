from __future__ import annotations

from celery import Celery

from api.core.config import settings


celery_app = Celery("lucknow_events")
celery_app.conf.update(
    broker_url=settings.REDIS_URL,
    result_backend=settings.REDIS_URL,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_default_queue="default",
)

celery_app.autodiscover_tasks([
    "workers.tasks.crawl",
    "workers.tasks.pipeline",
    "workers.tasks.feeds",
    "workers.tasks.submissions",
    "workers.tasks.discovery",
    "workers.tasks.watchlist",
])

# Celery Beat schedule lives here (import side-effect).
from workers.schedules import CELERYBEAT_SCHEDULE  # noqa: E402

celery_app.conf.beat_schedule = CELERYBEAT_SCHEDULE


@celery_app.task(name="workers.tasks.debug.ping")
def ping() -> str:
    return "pong"


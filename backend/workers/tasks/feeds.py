from __future__ import annotations

import structlog
from celery import shared_task


log = structlog.get_logger(__name__)


@shared_task(name="workers.tasks.feeds.rebuild_all_feeds")
def rebuild_all_feeds() -> dict:
    # Placeholder: in MVP, feeds are generated on-request in FastAPI.
    # Later we will materialize JSON + ICS to storage/cdn for caching.
    log.info("rebuild_all_feeds.start")
    return {"ok": True, "message": "rebuild_all_feeds stub"}


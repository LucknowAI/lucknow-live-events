from __future__ import annotations

from celery.schedules import crontab

CELERYBEAT_SCHEDULE = {
    # Lightweight feed rebuild — every 30 minutes (no DB writes, just JSON/ICS regen)
    "rebuild-feeds-every-30min": {
        "task": "workers.tasks.feeds.rebuild_all_feeds",
        "schedule": crontab(minute="*/30"),
    },
}

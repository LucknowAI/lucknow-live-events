from __future__ import annotations

from celery.schedules import crontab

CELERYBEAT_SCHEDULE = {
    # Full crawl of all enabled sources — every 12 hours (00:00 and 12:00 IST)
    "crawl-all-sources-every-12h": {
        "task": "workers.tasks.crawl.crawl_all_sources",
        "schedule": crontab(hour="*/12", minute=0),
    },
    # Lightweight feed rebuild — every 30 minutes (no DB writes, just JSON/ICS regen)
    "rebuild-feeds-every-30min": {
        "task": "workers.tasks.feeds.rebuild_all_feeds",
        "schedule": crontab(minute="*/30"),
    },
    # Expire events that ended >48h ago — runs daily at 3 AM IST
    "expire-past-events-daily": {
        "task": "workers.tasks.crawl.expire_past_events",
        "schedule": crontab(hour=3, minute=0),
    },
    # AI-powered discovery of new Lucknow event URLs — every 12 hours
    "auto-discover-events": {
        "task": "workers.tasks.discovery.auto_discover_events",
        "schedule": crontab(hour="*/12", minute=30),
    },
    # Refresh watchlist sources (single-event URLs) — every 12 hours
    "refresh-watchlist-sources": {
        "task": "workers.tasks.watchlist.refresh_watchlist_sources",
        "schedule": crontab(hour="*/12", minute=45),
    },
}

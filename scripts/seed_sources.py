#!/usr/bin/env python
"""Seed the database with initial scraping sources (requirements §17).

Usage (inside the api container):
    python scripts/seed_sources.py
"""
from __future__ import annotations

import asyncio
import sys
import os

# Ensure the backend package root is on the path regardless of where this is called from.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.core.database import SessionLocal
from api.models.source import Source


log = structlog.get_logger(__name__)

INITIAL_SOURCES = [
    {
        "name": "Static Test Source (dev)",
        "platform": "static",
        "base_url": "static://lucknow-events/dev-seed",
        "crawl_strategy": "static",
        "trust_score": 0.99,
        "enabled": True,
        "crawl_interval_hours": 24,
        "config_json": {
            "events": [
                {
                    "_id": "static-demo-1",
                    "title": "Lucknow Tech Events – Demo Meetup",
                    "start_at": "2026-06-01T11:00:00+05:30",
                    "end_at": "2026-06-01T13:00:00+05:30",
                    "mode": "offline",
                    "description": "Demo event seeded for end-to-end testing of scraper → pipeline → API → UI.",
                    "canonical_url": "https://example.com/lucknow-tech-events-demo",
                    "registration_url": "https://example.com/lucknow-tech-events-demo#register",
                    "city": "Lucknow",
                    "locality": "Gomti Nagar",
                    "venue_name": "Demo Venue, Lucknow",
                    "community_name": "Lucknow Developers",
                    "organizer_name": "Lucknow Developers",
                    "is_free": True,
                    "event_type": "meetup",
                    "topics": ["community", "networking"],
                }
            ]
        },
    },
    {
        "name": "Meetup Lucknow (Tech)",
        "platform": "meetup",
        "base_url": "https://www.meetup.com/find/",
        "crawl_strategy": "graphql_or_playwright",
        "trust_score": 0.72,
        "enabled": False,
        "crawl_interval_hours": 6,
        "config_json": {
            "find_url": "https://www.meetup.com/find/?source=EVENTS&location=in--Lucknow--India&distance=twentyFiveMiles",
            "max_items": 12,
        },
    },
    {
        "name": "GDG Lucknow",
        "platform": "gdg",
        "base_url": "https://gdg.community.dev/gdg-lucknow/",
        "crawl_strategy": "json_feed",
        "trust_score": 0.95,
        "enabled": True,
        "crawl_interval_hours": 6,
        "config_json": {"chapter_slug": "gdg-lucknow"},
    },
    {
        "name": "GDG on Campus SRMCEM (Lucknow)",
        "platform": "gdg",
        "base_url": "https://gdg.community.dev/gdg-on-campus-shri-ramswaroop-memorial-college-of-engineering-and-management-lucknow-india/",
        "crawl_strategy": "json_feed",
        "trust_score": 0.88,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {
            "chapter_slug": "gdg-on-campus-shri-ramswaroop-memorial-college-of-engineering-and-management-lucknow-india"
        },
    },
    {
        "name": "GDG on Campus IIIT Lucknow",
        "platform": "gdg",
        "base_url": "https://gdg.community.dev/gdg-on-campus-indian-institute-of-information-technology-lucknow-india/",
        "crawl_strategy": "json_feed",
        "trust_score": 0.90,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {
            "chapter_slug": "gdg-on-campus-indian-institute-of-information-technology-lucknow-india"
        },
    },
    {
        "name": "GDG on Campus BBDNIIT (Lucknow)",
        "platform": "gdg",
        "base_url": "https://gdg.community.dev/gdg-on-campus-babu-banarasi-das-northern-india-institute-of-technology-lucknow-india/",
        "crawl_strategy": "json_feed",
        "trust_score": 0.86,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {
            "chapter_slug": "gdg-on-campus-babu-banarasi-das-northern-india-institute-of-technology-lucknow-india"
        },
    },
    {
        "name": "GDSC IIIT Lucknow",
        "platform": "gdg",
        "base_url": "https://gdg.community.dev/gdsc-iiit-lucknow/",
        "crawl_strategy": "json_feed",
        "trust_score": 0.90,
        "enabled": True,
        "crawl_interval_hours": 6,
        "config_json": {"chapter_slug": "gdsc-iiit-lucknow"},
    },
    {
        "name": "GDG Lucknow (Commudle)",
        "platform": "commudle",
        "base_url": "https://www.commudle.com/communities/gdg-lucknow/events",
        "crawl_strategy": "playwright",
        "trust_score": 0.90,
        "enabled": True,
        "crawl_interval_hours": 6,
        "config_json": {"max_items": 15},
    },
    {
        "name": "AI Community Lucknow (TFUG Lucknow) – Commudle",
        "platform": "commudle",
        "base_url": "https://www.commudle.com/communities/tfug-lucknow/events",
        "crawl_strategy": "playwright",
        "trust_score": 0.90,
        "enabled": True,
        "crawl_interval_hours": 6,
        "config_json": {"max_items": 15},
    },
    {
        "name": "Cloud Native Lucknow (CNCF)",
        "platform": "generic",
        "base_url": "https://community.cncf.io/cloud-native-lucknow/",
        "crawl_strategy": "playwright",
        "trust_score": 0.88,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {"submitted_via": "seed_sources"},
    },
    {
        "name": "Lucknow AI Labs (Meetup)",
        "platform": "meetup",
        "base_url": "https://www.meetup.com/lucknow-ai-labs/",
        "crawl_strategy": "graphql_or_playwright",
        "trust_score": 0.82,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {"group_url": "https://www.meetup.com/lucknow-ai-labs/", "max_items": 12},
    },
    {
        "name": "Docker Lucknow (Meetup)",
        "platform": "meetup",
        "base_url": "https://www.meetup.com/docker-group-lucknow/",
        "crawl_strategy": "graphql_or_playwright",
        "trust_score": 0.78,
        "enabled": False,
        "crawl_interval_hours": 24,
        "config_json": {"group_url": "https://www.meetup.com/docker-group-lucknow/", "max_items": 12},
    },
    {
        "name": "AWS Cloud Club – University of Lucknow (Meetup)",
        "platform": "meetup",
        "base_url": "https://www.meetup.com/aws-cloud-club-university-of-lucknow/",
        "crawl_strategy": "graphql_or_playwright",
        "trust_score": 0.80,
        "enabled": True,
        "crawl_interval_hours": 24,
        "config_json": {
            "group_url": "https://www.meetup.com/aws-cloud-club-university-of-lucknow/",
            "max_items": 12,
        },
    },
    {
        "name": "HackoFiesta (IIIT Lucknow) – Official Site",
        "platform": "generic",
        "base_url": "https://www.hackofiesta.com/",
        "crawl_strategy": "playwright",
        "trust_score": 0.86,
        "enabled": True,
        "crawl_interval_hours": 24,
        "config_json": {"submitted_via": "seed_sources"},
    },
    {
        "name": "HackoFiesta 6.1 (Devfolio)",
        "platform": "generic",
        "base_url": "https://hackofiesta-6-1.devfolio.co/",
        "crawl_strategy": "playwright",
        "trust_score": 0.84,
        "enabled": True,
        "crawl_interval_hours": 24,
        "config_json": {"submitted_via": "seed_sources"},
    },
    {
        "name": "GitHub Copilot Dev Days – Lucknow (Luma)",
        "platform": "generic",
        "base_url": "https://lu.ma/xtxua1jl",
        "crawl_strategy": "playwright",
        "trust_score": 0.75,
        "enabled": False,
        "crawl_interval_hours": 24,
        "config_json": {"submitted_via": "seed_sources"},
    },
    {
        "name": "Commudle Lucknow Events",
        "platform": "commudle",
        "base_url": "https://www.commudle.com/events",
        "crawl_strategy": "playwright",
        "trust_score": 0.80,
        "enabled": False,
        "crawl_interval_hours": 6,
        "config_json": {"city_filter": "lucknow", "max_items": 10},
    },
    {
        "name": "Devfolio India Hackathons",
        "platform": "devfolio",
        "base_url": "https://devfolio.co/hackathons",
        "crawl_strategy": "playwright",
        "trust_score": 0.75,
        "enabled": False,
        "crawl_interval_hours": 6,
        "config_json": {"location_filter": "lucknow", "max_items": 10},
    },
    {
        "name": "Unstop Lucknow",
        "platform": "unstop",
        "base_url": "https://unstop.com/competitions",
        "crawl_strategy": "api_then_playwright",
        "trust_score": 0.70,
        "enabled": True,
        "crawl_interval_hours": 6,
        "config_json": {"location": "Lucknow", "max_items": 20},
    },
    # ── FOSS United ──────────────────────────────────────────────────────────
    {
        "name": "FOSS United Lucknow (RSS)",
        "platform": "fossunited",
        "base_url": "https://fossunited.org/c/lucknow",
        "crawl_strategy": "rss",
        "trust_score": 0.92,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {
            "rss_url": "https://fossunited.org/events/timeline/rss",
            "chapter_path": "lucknow",
            "max_items": 20,
        },
    },
    {
        "name": "LucknowFOSS 2.0 (FOSS United)",
        "platform": "generic",
        "base_url": "https://fossunited.org/c/lucknow/2026",
        "crawl_strategy": "playwright",
        "trust_score": 0.88,
        "enabled": True,
        "crawl_interval_hours": 24,
        "config_json": {"submitted_via": "seed_sources"},
    },
    # ── AI / ML Community ────────────────────────────────────────────────────
    {
        "name": "AI Community Lucknow (TFUG) – Main Site",
        "platform": "generic",
        "base_url": "https://aicommunity.lucknow.dev",
        "crawl_strategy": "playwright",
        "trust_score": 0.88,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {"submitted_via": "seed_sources"},
    },
    {
        "name": "TFUG Lucknow – Commudle Community Page",
        "platform": "commudle",
        "base_url": "https://www.commudle.com/communities/tfug-lucknow",
        "crawl_strategy": "playwright",
        "trust_score": 0.88,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {"max_items": 15},
    },
    # ── Lucknow AI Labs ──────────────────────────────────────────────────────
    {
        "name": "Lucknow AI Labs – Official Site",
        "platform": "generic",
        "base_url": "http://lucknowai.org",
        "crawl_strategy": "playwright",
        "trust_score": 0.85,
        "enabled": True,
        "crawl_interval_hours": 24,
        "config_json": {"submitted_via": "seed_sources"},
    },
    # ── GDG on Campus (additional chapters) ─────────────────────────────────
    {
        "name": "GDG on Campus BBDITM (Lucknow)",
        "platform": "gdg",
        "base_url": "https://gdg.community.dev/gdg-on-campus-bbditm-lucknow/",
        "crawl_strategy": "json_feed",
        "trust_score": 0.86,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {"chapter_slug": "gdg-on-campus-bbditm-lucknow", "max_items": 10},
    },
    {
        "name": "GDG on Campus BNCET Lucknow",
        "platform": "gdg",
        "base_url": "https://gdg.community.dev/gdg-on-campus-bncet-lucknow/",
        "crawl_strategy": "json_feed",
        "trust_score": 0.84,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {"chapter_slug": "gdg-on-campus-bncet-lucknow", "max_items": 10},
    },
    {
        "name": "GDG on Campus Integral University (Lucknow)",
        "platform": "gdg",
        "base_url": "https://gdg.community.dev/gdg-on-campus-integral-university-lucknow/",
        "crawl_strategy": "json_feed",
        "trust_score": 0.84,
        "enabled": True,
        "crawl_interval_hours": 12,
        "config_json": {
            "chapter_slug": "gdg-on-campus-integral-university-lucknow",
            "max_items": 10,
        },
    },
    # ── IIIT Lucknow Student Events ──────────────────────────────────────────
    {
        "name": "E-Summit IIIT Lucknow",
        "platform": "generic",
        "base_url": "https://esummit.iiitl.ac.in/",
        "crawl_strategy": "playwright",
        "trust_score": 0.82,
        "enabled": True,
        "crawl_interval_hours": 24,
        "config_json": {"submitted_via": "seed_sources"},
    },
    {
        "name": "AXIOS IIIT Lucknow (Annual Fest)",
        "platform": "generic",
        "base_url": "https://axios.iiitl.ac.in/",
        "crawl_strategy": "playwright",
        "trust_score": 0.80,
        "enabled": True,
        "crawl_interval_hours": 48,
        "config_json": {"submitted_via": "seed_sources"},
    },
    # ── AWS User Group ───────────────────────────────────────────────────────
    {
        "name": "AWS User Group Lucknow (Meetup)",
        "platform": "meetup",
        "base_url": "https://www.meetup.com/aws-user-group-lucknow/",
        "crawl_strategy": "graphql_or_playwright",
        "trust_score": 0.82,
        "enabled": True,
        "crawl_interval_hours": 24,
        "config_json": {
            "group_url": "https://www.meetup.com/aws-user-group-lucknow/",
            "max_items": 12,
        },
    },
]


async def seed() -> None:
    async with SessionLocal() as db:
        for src_data in INITIAL_SOURCES:
            existing = (
                await db.execute(
                    select(Source).where(Source.name == src_data["name"])
                )
            ).scalar_one_or_none()

            if existing:
                log.info("seed.already_exists", name=src_data["name"])
                continue

            import uuid
            src = Source(id=str(uuid.uuid4()), **src_data)
            db.add(src)
            log.info("seed.inserted", name=src_data["name"])

        await db.commit()
    log.info("seed.done", total=len(INITIAL_SOURCES))


if __name__ == "__main__":
    asyncio.run(seed())

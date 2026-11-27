#!/usr/bin/env python
"""Seed the database with the engine's source rows.

Usage, from backend/ (VPS) or inside the api container:
    python ../scripts/seed_sources.py
    python scripts/seed_sources.py
"""
from __future__ import annotations

import asyncio
import sys
import os

# Put the backend package root on the path, whatever the working directory.
# In the repo it is ../backend; the dev container mounts scripts/ inside it (..).
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(_here, "..", "backend"), os.path.join(_here, "..")]

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from api.core.database import SessionLocal
from api.models.source import Source


log = structlog.get_logger(__name__)

INITIAL_SOURCES = [
    {
        "name": "Unstop Lucknow",
        "platform": "unstop",
        "base_url": "https://unstop.com/competitions",
        "crawl_strategy": "api_then_playwright",
        "trust_score": 0.70,
        "enabled": True,
        "crawl_interval_hours": 6,
        "config_json": {
            "location": "Lucknow",
            "max_items": 20,
            "opportunity_types": ["hackathons", "workshops"],
        },
    },
]


INITIAL_SOURCES.append(
    {
        "name": "Luma Lucknow",
        "platform": "luma",
        "base_url": "https://luma.com/discover",
        "enabled": True,
        "config_json": {
            "latitude": 26.85,
            "longitude": 80.95,
            "categories": ["Tech"],
            "allowed_hosts": [],  # Stable usr-... API IDs, not display names.
        },
    }
)


# GDG chapters read by the engine's Bevy adapter (engine/sources/bevy.py).
# The chapter id is in each chapter page's source (`chapter_id = '1227'`).
# Names end in "(Bevy)" because seeding skips names that already exist, and
# older databases hold `gdg` rows under the plain chapter names.
BEVY_CHAPTERS = [
    ("GDG Lucknow", 1227, "gdg-lucknow"),
    ("GDG on Campus SRMCEM", 1928, "gdg-on-campus-shri-ramswaroop-memorial-college-of-engineering-and-management-lucknow-india"),
    ("GDG on Campus BBDITM", 1834, "gdg-on-campus-babu-banarasi-das-institute-of-technology-and-management-lucknow-india"),
    ("GDG on Campus BBDNIIT", 2207, "gdg-on-campus-babu-banarasi-das-northern-india-institute-of-technology-lucknow-india"),
    ("GDG on Campus BNCET", 2001, "gdg-on-campus-bn-college-of-engineering-technology-lucknow-india"),
    ("GDG on Campus Bansal IET", 2057, "gdg-on-campus-bansal-institute-of-engineering-technology-lucknow-india"),
    ("GDG on Campus Integral University", 1952, "gdg-on-campus-integral-university-lucknow-india"),
    ("GDG on Campus IIIT Lucknow", 1717, "gdg-on-campus-indian-institute-of-information-technology-lucknow-india"),
]

INITIAL_SOURCES += [
    {
        "name": f"{name} (Bevy)",
        "platform": "bevy",
        "base_url": f"https://gdg.community.dev/{slug}/",
        "enabled": True,
        # Lucknow chapters, so their online events are allowed too.
        "config_json": {"chapter_id": chapter_id, "lucknow_community": True},
    }
    for name, chapter_id, slug in BEVY_CHAPTERS
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
                if (
                    existing.platform == src_data["platform"] == "unstop"
                    and "opportunity_types" not in existing.config_json
                ):
                    existing.config_json = {
                        **existing.config_json,
                        "opportunity_types": src_data["config_json"]["opportunity_types"],
                    }
                    log.info("seed.config_upgraded", name=src_data["name"])
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

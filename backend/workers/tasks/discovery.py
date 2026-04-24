"""Celery task: Automated Agent to discover Lucknow tech events via Gemini Search Grounding."""
from __future__ import annotations

import asyncio
import datetime
import re
from typing import Any

import structlog
from celery import shared_task
from google.genai import types

from api.core.database import SessionLocal

log = structlog.get_logger(__name__)

# ─── Listing-page blocklist ───────────────────────────────────────────────────
# Root domains and browse/listing paths that are never individual event pages.
# Even if the AI returns them, we drop them here.
_LISTING_BLOCKLIST = re.compile(
    r"""
    (^https?://(www\.)?                     # root domain — no path
        (unstop\.com|devfolio\.co|lu\.ma|commudle\.com|
         meetup\.com|townscript\.com|konfhub\.com|
         gdg\.community\.dev|fossunited\.org|
         eventbrite\.(com|in)|
         hackathon\.io|hackerearth\.com)
    /?$)
    |
    (/competitions/?$|/hackathons/?$|/events/?$|
     /browse/?|/explore/?|/find/?|/search/?|
     /communities/?$|/chapters/?$|/groups/?$)
    """,
    re.VERBOSE | re.IGNORECASE,
)

_DISCOVERY_SYSTEM_PROMPT = """\
You are an intelligent event discovery agent for the Lucknow Tech Events aggregator.

Your mission: Find the direct URLs of INDIVIDUAL tech event pages for events happening
in or around Lucknow, UP, India — or online events hosted by Lucknow-based communities —
over the next 4 months.

## How to approach this

Think before you search. Design your own search strategy:
1. Consider which tech communities, colleges, and platforms are active in Lucknow.
2. Write targeted queries that surface individual event pages (not listing pages).
3. Use Google Search to execute those queries.
4. From the search results, extract only the URLs that lead to a SINGLE, SPECIFIC event.

## Active Lucknow tech communities to focus on
- GDG Lucknow, GDG on Campus chapters (IIIT Lucknow, SRMCEM, BBDNIIT, BBDITM, Integral University, BNCET)
- TFUG Lucknow / AI Community Lucknow
- FOSS United Lucknow
- AWS User Group Lucknow / AWS Cloud Club
- Lucknow AI Labs
- CNCF Cloud Native Lucknow
- College fests: HackoFiesta (IIIT Lucknow), AXIOS (IIIT Lucknow), E-Summit IIIT, Kalpathon (BBDU)

## Platforms where their events are listed
lu.ma, commudle.com, gdg.community.dev, devfolio.co, unstop.com,
meetup.com, townscript.com, fossunited.org, rifio.dev

## URL rules — ONLY return a URL if it:
- Points to ONE specific, named event (e.g. devfolio.co/events/hackofiesta-6, lu.ma/abc123)
- Has a recognisable event slug or ID in the path (not just /events or /competitions)
- Is NOT a platform homepage or root domain
- Is NOT a browse, search, listing, or directory page (/events, /hackathons, /browse, /competitions, /find)
- Is NOT a blog post, news article, job listing, or community homepage

## Output format
Return your final answer as a plain list of URLs — one URL per line.
Do NOT include markdown, bullet points, numbering, or any explanation.
Only the raw URLs, one per line.
"""


def _build_month_window(months: int = 4) -> tuple[list[str], str]:
    """Return a list of 'Month YYYY' strings for the next N months."""
    now = datetime.datetime.now()
    month_labels = []
    for i in range(months):
        m = (now.month - 1 + i) % 12 + 1
        y = now.year + (now.month - 1 + i) // 12
        date_obj = datetime.date(y, m, 1)
        month_labels.append(date_obj.strftime("%B %Y"))
    return month_labels, " OR ".join(f'"{m}"' for m in month_labels)


def _extract_urls_from_text(text: str) -> list[str]:
    """Extract all https:// URLs from free-form text."""
    raw = re.findall(r"https?://[^\s\)\]\,\"\'<>]+", text)
    seen: set[str] = set()
    result: list[str] = []
    for url in raw:
        url = url.rstrip(".,;:/")
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


def _is_listing_page(url: str) -> bool:
    """Return True if the URL matches known listing/browse patterns."""
    return bool(_LISTING_BLOCKLIST.search(url))


@shared_task(
    bind=True,
    name="workers.tasks.discovery.auto_discover_events",
    max_retries=1,
)
def auto_discover_events(self, custom_queries: list[str] | None = None) -> dict[str, Any]:
    """Scheduled task to find new event URLs via AI and queue them for validation."""
    return asyncio.get_event_loop().run_until_complete(_async_discover(custom_queries))


async def _async_discover(custom_queries: list[str] | None = None) -> dict[str, Any]:
    from ai.gemini_client import get_client
    from api.services.submission_service import create_submission

    client = get_client()
    month_labels, months_str = _build_month_window(months=4)

    if custom_queries:
        # Admin-supplied custom queries: run each one individually (old behaviour)
        # but still parse text responses — no JSON constraint.
        async def _run_query(q: str) -> list[str]:
            try:
                resp = await client.aio.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=q,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        temperature=0.1,
                        system_instruction=_DISCOVERY_SYSTEM_PROMPT,
                    ),
                )
                return _extract_urls_from_text(resp.text or "")
            except Exception as exc:
                log.warning("discovery.query_error", error=str(exc))
                return []

        results_lists = await asyncio.gather(*(_run_query(q) for q in custom_queries))
        raw_urls = [u for sub in results_lists for u in sub]
        queries_run = len(custom_queries)

    else:
        # Default: single rich strategic prompt, let the agent decide its queries.
        prompt = (
            f"Today is {datetime.datetime.now().strftime('%d %B %Y')}.\n"
            f"Target window: {', '.join(month_labels)}.\n\n"
            "Using Google Search, find the URLs of individual tech event pages "
            "happening in Lucknow (or online but organised by Lucknow communities) "
            f"during {months_str}.\n\n"
            "Design your own search strategy. Run as many targeted searches as needed. "
            "Return only the final list of individual event page URLs — one per line."
        )
        raw_urls = []
        try:
            resp = await client.aio.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                    temperature=0.2,
                    system_instruction=_DISCOVERY_SYSTEM_PROMPT,
                ),
            )
            raw_urls = _extract_urls_from_text(resp.text or "")
        except Exception as exc:
            log.warning("discovery.agent_error", error=str(exc))
        queries_run = 1

    # ── Deduplicate and filter listing pages ──────────────────────────────────
    seen: set[str] = set()
    urls: list[str] = []
    listing_dropped = 0
    for url in raw_urls:
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        if url in seen:
            continue
        if _is_listing_page(url):
            listing_dropped += 1
            log.debug("discovery.listing_dropped", url=url)
            continue
        seen.add(url)
        urls.append(url)

    log.info(
        "discovery.search_success",
        total_urls=len(urls),
        listing_dropped=listing_dropped,
    )

    results = {
        "total_found": len(urls),
        "new": 0,
        "duplicate": 0,
        "error": 0,
        "listing_dropped": listing_dropped,
        "queries_run": queries_run,
    }

    async with SessionLocal() as db:
        for url in urls:
            try:
                await create_submission(
                    db,
                    event_url=url,
                    submitter_name="AI Discovery Agent",
                    submitter_email="agent@nawab.ai",
                    notes="Automatically discovered via Gemini Search Grounding",
                )
                results["new"] += 1
            except Exception as e:
                log.warning("discovery.process_url_failed", url=url, error=str(e))
                results["error"] += 1

    log.info("discovery.completed", **results)
    return results

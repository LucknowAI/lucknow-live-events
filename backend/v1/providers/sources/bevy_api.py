"""T0 enumerator for Bevy-hosted communities (the platform GDG runs on).

**Why this adapter exists at all, and why it is built first.** One HTTP request returns
every event for a chapter, already structured, with an ISO-8601 start time carrying the
correct UTC offset. No browser, no HTML parsing, no LLM, no confidence score. Everything
this adapter covers is coverage the paid search path never has to pay for — which is the
whole "invert ingestion: push before pull" argument in one endpoint.

Verified live on 2026-08-08:

    GET /api/event/?chapter=1227&fields=id,title,start_date,url&limit=3
    → {"links": {...}, "pagination": {"page_size": 500, ...}, "count": 34, "results": [...]}

**The gotcha this adapter is mostly about.** Unknown query parameters are *silently
ignored*, not rejected:

    GET /api/event/?chapter=1227&fields=id&limit=1     → {"count": 34,    ...}
    GET /api/event/?city=<any-value>&fields=id&limit=1 → {"count": 70881, ...}

(`city` is not a parameter this API has. The value is irrelevant — that is the point.)

A typo'd filter does not produce an error. It produces HTTP 200 and the entire global
firehose — 70,881 events — and the first page of it looks completely normal. So the
adapter asserts the response is *plausibly filtered* by count, before looking at a single
row, and fails loudly rather than handing 70,000 candidates to the pipeline.

The chapter id is config data (`external_ref`). It cannot be looked up through the API —
`GET /api/chapter/` answers *"You do not have permission to perform this action"* — so
`resolve_chapter_id` scrapes the one number out of the chapter page, which is a one-off
per chapter, not a per-run cost.
"""

from __future__ import annotations

import re
from typing import Any

from v1.contracts.discovery import DiscoveredItem, StrategyKind
from v1.contracts.errors import SourceEnumerationFailed
from v1.contracts.source import SourceKind
from v1.platform.logging import get_logger
from v1.platform.net import host_of
from v1.providers.sources.base import BaseSourceEnumerator

logger = get_logger("v1.sources.bevy")

EVENT_PATH = "/api/event/"

FIELDS = ("id", "title", "start_date", "end_date", "url", "status", "event_type_title")
"""Explicit field selection. The default response inlines the entire chapter object —
including its full HTML description — on every single row, so `fields=` cuts the payload
by roughly 95%. Bandwidth is the small win; the real one is that a 95% smaller payload is
one we can hold in a fixture and read in a review."""

PAGE_SIZE = 500
DEFAULT_UNFILTERED_SENTINEL = 5_000
"""If a chapter query comes back with at least this many events, the filter did not
apply. No single community has thousands of events; the global firehose has ~70,000."""

PUBLISHED_STATUSES = frozenset({"published", "live"})

_CHAPTER_ID_RE = re.compile(r"chapter_id\s*=\s*['\"](\d+)['\"]")


class BevyApiEnumerator(BaseSourceEnumerator):
    """Lists a Bevy chapter's events through the public, unauthenticated event API."""

    adapter = "bevy_api"
    kind = SourceKind.STRUCTURED

    @property
    def _base_url(self) -> str:
        base = (self.source.base_url or "").rstrip("/")
        if not base:
            raise SourceEnumerationFailed(
                f"source {self.source_id!r} (bevy_api) requires `base_url`",
                source=self.source_id,
            )
        return base

    @property
    def _chapter_id(self) -> str:
        ref = (self.source.external_ref or "").strip()
        if not ref:
            raise SourceEnumerationFailed(
                f"source {self.source_id!r} (bevy_api) requires `external_ref` — the "
                "numeric chapter id. Resolve it with `resolve_chapter_id`; it cannot be "
                "looked up through the API, which requires authentication for /api/chapter/",
                source=self.source_id,
            )
        return ref

    async def _enumerate(self) -> list[DiscoveredItem]:
        payload = await self.get_json(
            f"{self._base_url}{EVENT_PATH}",
            params={
                "chapter": self._chapter_id,
                "fields": ",".join(FIELDS),
                "limit": PAGE_SIZE,
            },
        )
        self._assert_filtered(payload)

        results = payload.get("results")
        if not isinstance(results, list):
            raise SourceEnumerationFailed(
                f"source {self.source_id!r}: Bevy response had no `results` array",
                source=self.source_id,
                keys=sorted(payload)[:10],
            )
        return [item for row in results if (item := self._to_item(row)) is not None]

    def _assert_filtered(self, payload: dict[str, Any]) -> None:
        """The silent-filter assertion. See the module docstring.

        **Why this checks the count and not each row's chapter.** Verifying per row would
        be strictly better, and it is not available: `chapter` is absent from every
        response, *including* when explicitly requested. Probed 2026-08-08:

            GET /api/event/?chapter=1227&fields=id,chapter,title&limit=2
            → results: [{"id": ..., "title": ..., "title_translated": null}]

        The `chapter` field is dropped exactly as silently as an unknown filter is
        ignored — the same behaviour, in the other direction. So there is no chapter
        attribution on the row to check against, and the count plus the host check below
        is the strongest assertion this API actually supports. Recorded here so nobody
        re-derives it and assumes it was an oversight.
        """
        count = payload.get("count")
        if not isinstance(count, int):
            raise SourceEnumerationFailed(
                f"source {self.source_id!r}: Bevy response had no integer `count`, so the "
                "filter cannot be verified. Refusing the result rather than trusting it",
                source=self.source_id,
                count=repr(count),
            )
        sentinel = self.source.unfiltered_sentinel or DEFAULT_UNFILTERED_SENTINEL
        if count >= sentinel:
            raise SourceEnumerationFailed(
                f"source {self.source_id!r}: Bevy returned {count} events for chapter "
                f"{self._chapter_id!r}, at or above the {sentinel} unfiltered sentinel. "
                "Bevy ignores unknown query parameters silently, so this is almost "
                "certainly the unfiltered global feed rather than one chapter",
                source=self.source_id,
                count=count,
                sentinel=sentinel,
                chapter=self._chapter_id,
            )

    def _to_item(self, row: object) -> DiscoveredItem | None:
        if not isinstance(row, dict):
            return None
        url = row.get("url")
        if not isinstance(url, str) or not url.strip():
            # A row with no URL is not a candidate — there is nothing to fetch, dedupe or
            # link to. Counted in the log so a source that starts omitting URLs is visible.
            logger.warning("bevy_row_without_url", source=self.source_id, event_id=row.get("id"))
            return None

        # Every event on a Bevy site is served from that site. A row pointing somewhere
        # else means the response is not what we asked for — a redirected host, a proxy,
        # or a payload from a different instance — and it must not become a candidate.
        if host_of(url) != host_of(self._base_url):
            logger.warning(
                "bevy_row_off_host",
                source=self.source_id,
                event_id=row.get("id"),
                url_host=host_of(url),
                expected_host=host_of(self._base_url),
            )
            return None

        status = str(row.get("status") or "").strip().lower()
        if status and status not in PUBLISHED_STATUSES:
            # Drafts and cancelled events are in the feed. Filtering here rather than
            # downstream keeps a cancelled event from ever becoming a candidate.
            return None
        if row.get("is_hidden") is True or row.get("is_test") is True:
            return None

        return DiscoveredItem(
            url=url.strip(),
            raw_url=url,
            strategy=StrategyKind.FOCUSED,
            origin=self.source_id,
            tier=self.tier,
            title=(row.get("title") or None),
            external_id=str(row["id"]) if row.get("id") is not None else None,
            # Carried forward verbatim, never interpreted here. This payload is what lets
            # extraction skip the LLM entirely for these events.
            structured=row,
        )


_CHAPTER_PAGE_MAX_BYTES = 4 * 1024 * 1024


async def resolve_chapter_id(enumerator: BaseSourceEnumerator, chapter_slug: str) -> str:
    """Scrape a chapter's numeric id out of its public page.

    Exposed as an operator tool (`GET /discovery/sources/resolve`), not called during a
    run. The id never changes, so resolving it once and writing it into `instance.yaml` is
    correct; re-scraping it on every run would be a fetch we do not need and a dependency
    on the page's markup that we do not want in the hot path.
    """
    base = (enumerator.source.base_url or "").rstrip("/")
    url = f"{base}/{chapter_slug.strip('/')}/"
    body = await enumerator.get_bytes(url, max_bytes=_CHAPTER_PAGE_MAX_BYTES)
    match = _CHAPTER_ID_RE.search(body.decode("utf-8", "replace"))
    if match is None:
        raise SourceEnumerationFailed(
            f"no chapter_id found on {url}. Either the slug is wrong (several plausible "
            "on-campus slugs return 404) or the page markup changed",
            url=url,
            chapter_slug=chapter_slug,
        )
    return match.group(1)

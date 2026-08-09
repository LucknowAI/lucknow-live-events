"""Source enumerators: the Bevy silent-filter assertion and sitemap parsing safety.

The two failure modes worth the most tests here are both cases where the naive
implementation *succeeds* and returns something wrong:

* Bevy answers a typo'd filter with HTTP 200 and the entire global firehose.
* A hostile sitemap parses fine and expands an entity bomb while doing it.
"""

from __future__ import annotations

import gzip

import httpx
import pytest
import respx

from v1.config.schema import SourceConfig
from v1.contracts.errors import (
    MalformedFeed,
    ProviderAuthError,
    RateLimited,
    SourceEnumerationFailed,
)
from v1.contracts.source import SourceTier
from v1.providers.sources.bevy_api import BevyApiEnumerator, resolve_chapter_id
from v1.providers.sources.sitemap import SitemapEnumerator, parse_sitemap

BEVY_URL = "https://events.example.com/api/event/"
SITEMAP_URL = "https://feeds.example.com/sitemap.xml"


def _bevy_source(**overrides: object) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "id": "demo",
            "adapter": "bevy_api",
            "tier": SourceTier.T0_API,
            "base_url": "https://events.example.com",
            "external_ref": "42",
            "unfiltered_sentinel": 5000,
            **overrides,
        }
    )


def _sitemap_source(**overrides: object) -> SourceConfig:
    return SourceConfig.model_validate(
        {
            "id": "feed",
            "adapter": "sitemap",
            "tier": SourceTier.T1_FEED,
            "url": SITEMAP_URL,
            **overrides,
        }
    )


def _bevy_payload(count: int, rows: list[dict] | None = None) -> dict:
    return {
        "links": {"next": None, "previous": None},
        "pagination": {"page_size": 500, "current_page": 1},
        "count": count,
        "results": rows if rows is not None else [],
    }


# ------------------------------------------------------------------------- bevy


@respx.mock
async def test_bevy_returns_structured_items() -> None:
    row = {
        "id": 127102,
        "title": "Build with AI",
        "start_date": "2026-09-04T06:00:00+05:30",
        "url": "https://events.example.com/events/details/build-with-ai/",
        "status": "Published",
    }
    respx.get(BEVY_URL).mock(return_value=httpx.Response(200, json=_bevy_payload(1, [row])))

    enumerator = BevyApiEnumerator(source=_bevy_source())
    items = await enumerator.enumerate_items()
    await enumerator.aclose()

    assert len(items) == 1
    assert items[0].external_id == "127102"
    assert items[0].tier is SourceTier.T0_API
    # The payload is carried forward verbatim; discarding it would turn a free,
    # confidence-1.0 result into a paid extraction later.
    assert items[0].structured == row


@respx.mock
async def test_bevy_refuses_an_unfiltered_response() -> None:
    """The gotcha this adapter exists for: unknown query params are ignored, so a typo'd
    filter answers HTTP 200 with ~70,000 global events."""
    respx.get(BEVY_URL).mock(return_value=httpx.Response(200, json=_bevy_payload(70881, [])))

    enumerator = BevyApiEnumerator(source=_bevy_source())
    with pytest.raises(SourceEnumerationFailed, match="unfiltered sentinel"):
        await enumerator.enumerate_items()
    await enumerator.aclose()


@respx.mock
async def test_bevy_refuses_a_response_with_no_count() -> None:
    """Without `count` the filter cannot be verified, so the result is not trusted."""
    respx.get(BEVY_URL).mock(return_value=httpx.Response(200, json={"results": []}))

    enumerator = BevyApiEnumerator(source=_bevy_source())
    with pytest.raises(SourceEnumerationFailed, match="integer `count`"):
        await enumerator.enumerate_items()
    await enumerator.aclose()


@respx.mock
async def test_bevy_drops_drafts_hidden_and_urlless_rows() -> None:
    rows = [
        {"id": 1, "url": "https://events.example.com/events/details/a/", "status": "Published"},
        {"id": 2, "url": "https://events.example.com/events/details/b/", "status": "Draft"},
        {"id": 3, "url": "https://events.example.com/events/details/c/", "is_hidden": True},
        {"id": 4, "title": "no url at all"},
    ]
    respx.get(BEVY_URL).mock(return_value=httpx.Response(200, json=_bevy_payload(4, rows)))

    enumerator = BevyApiEnumerator(source=_bevy_source())
    items = await enumerator.enumerate_items()
    await enumerator.aclose()

    assert [item.external_id for item in items] == ["1"]


@respx.mock
async def test_bevy_maps_status_codes_to_typed_errors() -> None:
    respx.get(BEVY_URL).mock(return_value=httpx.Response(403, text="nope"))
    enumerator = BevyApiEnumerator(source=_bevy_source())
    with pytest.raises(ProviderAuthError):
        await enumerator.enumerate_items()
    await enumerator.aclose()


@respx.mock
async def test_bevy_rate_limit_is_typed_and_retried() -> None:
    route = respx.get(BEVY_URL)
    route.side_effect = [
        httpx.Response(429, text="slow down"),
        httpx.Response(200, json=_bevy_payload(0, [])),
    ]
    enumerator = BevyApiEnumerator(source=_bevy_source())
    assert await enumerator.enumerate_items() == []
    await enumerator.aclose()
    assert route.call_count == 2


@respx.mock
async def test_bevy_gives_up_on_persistent_rate_limit() -> None:
    respx.get(BEVY_URL).mock(return_value=httpx.Response(429))
    enumerator = BevyApiEnumerator(source=_bevy_source())
    with pytest.raises(RateLimited):
        await enumerator.enumerate_items()
    await enumerator.aclose()


@respx.mock
async def test_resolve_chapter_id_scrapes_the_page() -> None:
    """`/api/chapter/` requires auth, so the id has to come out of the HTML."""
    respx.get("https://events.example.com/gdg-somewhere/").mock(
        return_value=httpx.Response(200, text="<script>var chapter_id = '1227';</script>")
    )
    enumerator = BevyApiEnumerator(source=_bevy_source())
    assert await resolve_chapter_id(enumerator, "gdg-somewhere") == "1227"
    await enumerator.aclose()


def test_bevy_without_external_ref_fails_loudly() -> None:
    enumerator = BevyApiEnumerator(source=_bevy_source(external_ref=None))
    with pytest.raises(SourceEnumerationFailed, match="external_ref"):
        _ = enumerator._chapter_id


# ---------------------------------------------------------------------- sitemap

_URLSET = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://site.example.com/communities/testville-devs/events/one</loc></url>
  <url><loc>https://site.example.com/communities/other/events/two</loc></url>
</urlset>"""

_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://feeds.example.com/sitemap_events.xml</loc></sitemap>
  <sitemap><loc>https://feeds.example.com/sitemap_users.xml</loc></sitemap>
</sitemapindex>"""

_BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
]>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>&lol2;</loc></url>
</urlset>"""


def test_parse_urlset() -> None:
    pages, children = parse_sitemap(_URLSET, url=SITEMAP_URL)
    assert len(pages) == 2
    assert children == []


def test_parse_index() -> None:
    pages, children = parse_sitemap(_INDEX, url=SITEMAP_URL)
    assert pages == []
    assert children[0].endswith("sitemap_events.xml")


def test_doctype_is_refused_before_parsing() -> None:
    """Python's stdlib parser blocks external entities but still expands internal ones, so
    an entity bomb is reachable. A sitemap has no legitimate use for a DTD."""
    with pytest.raises(MalformedFeed, match="DOCTYPE"):
        parse_sitemap(_BILLION_LAUGHS, url=SITEMAP_URL)


def test_gzip_is_transparently_handled() -> None:
    pages, _ = parse_sitemap(gzip.compress(_URLSET), url=SITEMAP_URL)
    assert len(pages) == 2


def test_decompression_bomb_is_capped() -> None:
    """`len(gzip.decompress(...))` is not a cap — by then the bomb has gone off."""
    bomb = gzip.compress(b"A" * (60 * 1024 * 1024))
    with pytest.raises(MalformedFeed, match="decompresses to more than"):
        parse_sitemap(bomb, url=SITEMAP_URL)


def test_spa_shell_served_at_sitemap_xml_is_reported_clearly() -> None:
    """Devfolio does exactly this. "Not XML" is a much better error than "0 URLs"."""
    with pytest.raises(MalformedFeed, match="root element"):
        parse_sitemap(b"<html><body>app</body></html>", url=SITEMAP_URL)


@respx.mock
async def test_sitemap_filters_by_include_pattern_without_fetching_pages() -> None:
    respx.get(SITEMAP_URL).mock(return_value=httpx.Response(200, content=_URLSET))

    enumerator = SitemapEnumerator(
        source=_sitemap_source(include_patterns=[r"/communities/testville-devs/events/"])
    )
    items = await enumerator.enumerate_items()
    await enumerator.aclose()

    assert [item.url for item in items] == [
        "https://site.example.com/communities/testville-devs/events/one"
    ]


@respx.mock
async def test_child_include_patterns_are_an_allowlist() -> None:
    """Measured on the real index: following every child pulled 205,336 URLs in 117s to
    find 178. An allowlist means the next child somebody adds is not crawled by default."""
    respx.get(SITEMAP_URL).mock(return_value=httpx.Response(200, content=_INDEX))
    events = respx.get("https://feeds.example.com/sitemap_events.xml").mock(
        return_value=httpx.Response(200, content=_URLSET)
    )
    users = respx.get("https://feeds.example.com/sitemap_users.xml").mock(
        return_value=httpx.Response(200, content=_URLSET)
    )

    enumerator = SitemapEnumerator(
        source=_sitemap_source(child_include_patterns=[r"sitemap_events\.xml$"])
    )
    await enumerator.enumerate_items()
    await enumerator.aclose()

    assert events.call_count == 1
    assert users.call_count == 0


@respx.mock
async def test_sitemap_child_pointing_at_a_private_address_is_skipped() -> None:
    """A sitemap index is third-party input naming what to fetch next."""
    hostile = b"""<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>http://169.254.169.254/latest/meta-data/</loc></sitemap>
    </sitemapindex>"""
    respx.get(SITEMAP_URL).mock(return_value=httpx.Response(200, content=hostile))

    enumerator = SitemapEnumerator(source=_sitemap_source())
    assert await enumerator.enumerate_items() == []
    await enumerator.aclose()

"""T1 enumerator: sitemap.xml, including sitemap indexes.

A sitemap is the cheapest possible discovery for a platform with no API — one request
returns every URL the site is willing to admit exists. Verified live on 2026-08-08:
`https://www.commudle.com/sitemap.xml` is a **sitemap index**, and its events child
`https://json.commudle.com/sitemaps/sitemap_events.xml` holds **5,058 `<url>` entries with
no `<lastmod>`**. So a sitemap gives us discovery, but not change detection — which is
worth knowing before Phase 3 designs conditional GET around it.

Because the community slug is in the path (`/communities/{slug}/events/{event-slug}`),
`include_patterns` reduces those 5,058 URLs to the relevant ones **before a single page is
fetched**. That filtering is the entire economic argument for this adapter.

## Parsing third-party XML safely

A sitemap is written by someone else, so parsing it is a trust boundary. Tested on this
machine's Python 3.14.6:

| Attack | `xml.etree.ElementTree` |
|---|---|
| External entity (`file:///etc/hostname`) | blocked — `ParseError: undefined entity` |
| Billion laughs (internal entity expansion) | **expanded as instructed** |

`defusedxml` is the usual answer and would work, but it is a new dependency for one
parser. A sitemap has **no legitimate use for a DTD**, so the stronger and cheaper fix is
to refuse any document containing `<!DOCTYPE` before parsing begins — an entity bomb needs
a DTD to declare its entities in. Combined with the response byte cap in the base class
and a separate cap on post-gunzip size, there is nowhere for the bomb to land.
"""

from __future__ import annotations

import gzip
import re
import zlib
from xml.etree import ElementTree

from v1.config.schema import SourceConfig
from v1.contracts.discovery import DiscoveredItem, StrategyKind
from v1.contracts.errors import MalformedFeed, SourceEnumerationFailed
from v1.contracts.source import SourceKind
from v1.platform.logging import get_logger
from v1.platform.net import is_fetchable
from v1.providers.sources.base import DEFAULT_USER_AGENT, BaseSourceEnumerator

logger = get_logger("v1.sources.sitemap")

NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"

MAX_URLS_PER_FILE = 50_000
"""The sitemaps.org limit. A file claiming more is either broken or hostile."""

MAX_DECOMPRESSED_BYTES = 50 * 1024 * 1024
"""sitemaps.org caps an uncompressed sitemap at 50 MB. Enforced during decompression, not
after it — checking `len(gzip.decompress(...))` means the bomb already went off."""

MAX_INDEX_DEPTH = 1
"""A sitemap index may point at sitemaps. It may not point at more indexes, as far as we
are willing to follow — the protocol discourages nesting and an unbounded walk of
attacker-supplied indexes is a crawl we never agreed to."""

MAX_CHILD_SITEMAPS = 20

_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_GZIP_MAGIC = b"\x1f\x8b"


def _decompress_capped(raw: bytes, *, url: str) -> bytes:
    """Gunzip with a hard output cap, refusing the bomb rather than allocating it."""
    decompressor = zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)
    try:
        out = decompressor.decompress(raw, MAX_DECOMPRESSED_BYTES + 1)
    except (zlib.error, OSError, gzip.BadGzipFile) as exc:
        raise MalformedFeed(f"{url} is not valid gzip: {exc}", url=url) from exc
    if len(out) > MAX_DECOMPRESSED_BYTES or decompressor.unconsumed_tail:
        raise MalformedFeed(
            f"{url} decompresses to more than {MAX_DECOMPRESSED_BYTES} bytes; refusing",
            url=url,
            limit_bytes=MAX_DECOMPRESSED_BYTES,
        )
    return out


def parse_sitemap(raw: bytes, *, url: str) -> tuple[list[str], list[str]]:
    """Return `(page_urls, child_sitemap_urls)` from one sitemap or sitemap index.

    Exactly one of the two lists is populated for a well-formed document. Both are
    returned so a caller never has to ask "which kind was it" twice.
    """
    if raw[:2] == _GZIP_MAGIC:
        raw = _decompress_capped(raw, url=url)

    if _DOCTYPE_RE.search(raw):
        # The WHOLE buffer, not a prefix. An earlier version scanned only the first 4 KB,
        # which is a window an attacker controls: pad the document with 4 KB of comments
        # or whitespace and the DTD sails past the check straight into the parser. The
        # scan is a single `bytes` search over an already-capped buffer, so there is no
        # reason to look at less than all of it.
        #
        # See the module docstring: no legitimate sitemap declares a DTD, and a DTD is
        # what an entity bomb needs.
        raise MalformedFeed(
            f"{url} contains a DOCTYPE declaration. Sitemaps have no legitimate use for "
            "one, and internal entity expansion is a denial-of-service vector in the "
            "standard library parser — refusing to parse it",
            url=url,
        )

    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise MalformedFeed(f"{url} is not parseable XML: {exc}", url=url) from exc

    tag = root.tag
    if tag == f"{NS}sitemapindex":
        children = [
            text.strip()
            for element in root.findall(f"{NS}sitemap/{NS}loc")
            if (text := element.text) and text.strip()
        ]
        return [], children[:MAX_CHILD_SITEMAPS]

    if tag == f"{NS}urlset":
        urls = [
            text.strip()
            for element in root.findall(f"{NS}url/{NS}loc")
            if (text := element.text) and text.strip()
        ]
        if len(urls) > MAX_URLS_PER_FILE:
            raise MalformedFeed(
                f"{url} declares {len(urls)} URLs, above the {MAX_URLS_PER_FILE} limit "
                "in the sitemap protocol",
                url=url,
                count=len(urls),
            )
        return urls, []

    raise MalformedFeed(
        f"{url} root element is {tag!r}, expected a sitemaps.org urlset or sitemapindex. "
        "A site that serves its SPA shell at /sitemap.xml looks exactly like this",
        url=url,
        root_tag=tag,
    )


class SitemapEnumerator(BaseSourceEnumerator):
    """Walks one sitemap (or index) and returns the URLs matching the source's patterns."""

    adapter = "sitemap"
    kind = SourceKind.URL_LIST

    def __init__(self, *, source: SourceConfig, user_agent: str = DEFAULT_USER_AGENT) -> None:
        super().__init__(source=source, user_agent=user_agent)
        self._include = [re.compile(pattern) for pattern in source.include_patterns]
        self._exclude = [re.compile(pattern) for pattern in source.exclude_patterns]
        self._child_include = [re.compile(pattern) for pattern in source.child_include_patterns]

    async def _enumerate(self) -> list[DiscoveredItem]:
        root_url = (self.source.url or "").strip()
        if not root_url:
            raise SourceEnumerationFailed(
                f"source {self.source_id!r} (sitemap) requires `url`", source=self.source_id
            )

        urls = await self._walk(root_url, depth=0)
        matched = [url for url in urls if self._matches(url)]

        logger.info(
            "sitemap_filtered",
            source=self.source_id,
            urls_total=len(urls),
            urls_matched=len(matched),
            include_patterns=len(self._include),
        )
        return [
            DiscoveredItem(
                url=url,
                raw_url=url,
                strategy=StrategyKind.FOCUSED,
                origin=self.source_id,
                tier=self.tier,
            )
            for url in matched
        ]

    async def _walk(self, url: str, *, depth: int) -> list[str]:
        raw = await self.get_bytes(url)
        pages, children = parse_sitemap(raw, url=url)
        if pages:
            return pages

        if depth >= MAX_INDEX_DEPTH:
            logger.warning(
                "sitemap_index_depth_exceeded",
                source=self.source_id,
                url=url,
                depth=depth,
                children=len(children),
            )
            return []

        collected: list[str] = []
        for child in children:
            if not is_fetchable(child):
                # A sitemap index is third-party input naming what to fetch next. One bad
                # entry is skipped and logged; it must not abort a legitimate index.
                logger.warning("sitemap_child_refused", source=self.source_id, child=child)
                continue
            if not self._child_wanted(child):
                logger.info("sitemap_child_skipped", source=self.source_id, child=child)
                continue
            collected.extend(await self._walk(child, depth=depth + 1))
        return collected

    def _child_wanted(self, child: str) -> bool:
        """Which children of an index are worth requesting.

        An **allowlist**, not a blocklist. Following everything Commudle's index lists
        pulled 205,336 URLs in 117 seconds to find the 178 we wanted; an exclude list would
        have to name every uninteresting child and would silently start crawling the next
        one somebody adds. With `child_include_patterns` empty, all children are followed,
        which is the right default for an index we know nothing about.
        """
        if self._child_include:
            return any(pattern.search(child) for pattern in self._child_include)
        return not any(pattern.search(child) for pattern in self._exclude)

    def _matches(self, url: str) -> bool:
        if not is_fetchable(url):
            return False
        if any(pattern.search(url) for pattern in self._exclude):
            return False
        if not self._include:
            return True
        return any(pattern.search(url) for pattern in self._include)

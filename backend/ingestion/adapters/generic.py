from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ingestion.adapters.base import BaseAdapter, ScrapedPage
from ingestion.adapters.playwright_util import playwright_render
from ingestion.normalizers.text import MAX_EXTRACTION_CHARS, clean_text, ensure_absolute_url, strip_tracking_params


class GenericAdapter(BaseAdapter):
    """
    Fallback for any URL-based source.
    Uses Playwright to render the page, extracts visible text, and
    routes it through the AI Extraction Agent (primary method).
    """

    platform = "generic"
    crawl_strategy = "playwright"

    async def fetch(self, source: dict[str, Any]) -> list[ScrapedPage]:
        url = source.get("base_url", "")
        if not url:
            raise ValueError("Generic source has no base_url")

        html = await playwright_render(url)
        return [
            ScrapedPage(
                url=url,
                html_or_json=html,
                fetched_at=datetime.now(timezone.utc),
                status_code=200,
                page_type="detail",
            )
        ]

    def extract_raw_events(self, page: ScrapedPage) -> list[dict[str, Any]]:
        """
        For the generic adapter the AI extraction agent is the primary parser,
        so we return the cleaned text as a single raw event payload.
        """
        html = str(page.html_or_json)

        # Try to preserve key metadata that tends to be lost when stripping HTML.
        poster_url = _extract_best_image_url(html, page.url)
        meta_desc = _extract_meta_description(html)
        json_ld = _extract_json_ld(html)

        visible_text = clean_text(html, max_chars=MAX_EXTRACTION_CHARS)
        # Build an extraction-friendly payload for the AI agent.
        # IMPORTANT: avoid human-ish label lines that can get mis-read as the event title.
        # Provide a compact JSON metadata block + a clear text block.
        meta: dict[str, Any] = {}
        if meta_desc:
            meta["meta_description"] = meta_desc
        if poster_url:
            meta["poster_url"] = poster_url
        if json_ld:
            meta["json_ld"] = json_ld

        if meta:
            import json as _json

            text = _json.dumps({"page_url": page.url, "metadata": meta}, ensure_ascii=False)
            text = text + "\n\n" + visible_text
        else:
            text = visible_text
        text = text[:MAX_EXTRACTION_CHARS]

        raw: dict[str, Any] = {"_cleaned_text": text, "canonical_url": page.url}
        # Preserve structured signals so the pipeline can classify pages
        # (detail vs listing/noise) without relying entirely on the LLM output.
        if meta_desc:
            raw["_meta_description"] = meta_desc
        if json_ld:
            raw["_json_ld"] = json_ld
        if poster_url:
            raw["poster_url"] = poster_url
        return [raw]

    def get_external_id(self, raw: dict[str, Any]) -> str | None:
        return None


def _extract_meta_image(html: str) -> str | None:
    import re

    # Prefer OG image; fallback to twitter image.
    pats = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+property=["\']og:image:url["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for p in pats:
        m = re.search(p, html, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _extract_best_image_url(html: str, page_url: str) -> str | None:
    """
    Choose the best event poster candidate from:
      - JSON-LD `image`
      - OpenGraph/Twitter meta tags

    Then normalize it:
      - make absolute (handles relative URLs)
      - strip tracking params (utm, fbclid, gclid)
    """
    candidates: list[str] = []
    candidates.extend(_extract_json_ld_images(html))
    candidates.extend(_extract_meta_images(html))

    normalized: list[str] = []
    for u in candidates:
        u = (u or "").strip()
        if not u:
            continue
        abs_u = ensure_absolute_url(u, base_url=_base_for_absolute(page_url))
        abs_u = strip_tracking_params(abs_u)
        if _looks_like_image_url(abs_u):
            normalized.append(abs_u)

    if not normalized:
        return None

    # Stable de-dupe while preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for u in normalized:
        if u not in seen:
            seen.add(u)
            uniq.append(u)

    return max(uniq, key=_image_score)


def _extract_meta_images(html: str) -> list[str]:
    import re

    # Some sites output multiple og:image tags; collect all.
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+property=["\']og:image:url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']twitter:image:src["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    out: list[str] = []
    for p in patterns:
        out.extend(m.group(1).strip() for m in re.finditer(p, html, flags=re.IGNORECASE))
    return out


def _extract_json_ld_images(html: str) -> list[str]:
    import json

    blocks = _extract_json_ld_blocks(html)
    out: list[str] = []
    for b in blocks:
        try:
            data = json.loads(b)
        except Exception:
            continue

        # JSON-LD can be dict or list.
        items: list[Any] = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = [data]

        for item in items:
            if not isinstance(item, dict):
                continue
            out.extend(_json_ld_image_values(item))
            # Also handle @graph containers.
            graph = item.get("@graph")
            if isinstance(graph, list):
                for g in graph:
                    if isinstance(g, dict):
                        out.extend(_json_ld_image_values(g))
    return out


def _json_ld_image_values(obj: dict[str, Any]) -> list[str]:
    # Prefer Event-type-ish nodes, but don’t require it (many sites omit @type).
    img = obj.get("image")
    out: list[str] = []

    def _add(v: Any) -> None:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            u = v.get("url") or v.get("@id")
            if isinstance(u, str):
                out.append(u)
        elif isinstance(v, list):
            for x in v:
                _add(x)

    _add(img)
    return out


def _extract_json_ld(html: str) -> str | None:
    import re

    # Extract the first few JSON-LD blocks; keep it bounded.
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not blocks:
        return None
    joined = "\n\n".join(b.strip() for b in blocks if b and b.strip())
    return joined[:6000] if joined else None


def _extract_json_ld_blocks(html: str) -> list[str]:
    import re

    return [
        b.strip()
        for b in re.findall(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if b and b.strip()
    ][:6]


def _extract_meta_description(html: str) -> str | None:
    import re

    m = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        flags=re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            flags=re.IGNORECASE,
        )
    return m.group(1).strip()[:1000] if m else None


def _looks_like_image_url(url: str) -> bool:
    u = (url or "").lower()
    if not u:
        return False
    if u.startswith("data:"):
        return False
    # Avoid obvious non-images.
    if any(ext in u for ext in (".mp4", ".webm", ".m3u8")):
        return False
    return True


def _image_score(url: str) -> int:
    """
    Heuristic scoring:
      - prefer large/banner-ish images
      - demote logos/icons/favicons/sprites
    """
    u = (url or "").lower()
    score = 0

    if u.startswith("https://"):
        score += 5
    elif u.startswith("http://"):
        score += 2

    if any(k in u for k in ("og", "banner", "cover", "poster", "hero", "event", "image")):
        score += 6

    # Typical big OG sizes or CDN transforms
    if any(k in u for k in ("1200", "1080", "1600", "1920", "2000", "2048")):
        score += 4

    if any(k in u for k in ("logo", "favicon", "icon", "sprite", "placeholder", "default")):
        score -= 10

    # Prefer common raster formats slightly.
    if any(u.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
        score += 2
    if u.endswith(".svg"):
        score -= 2

    return score


def _base_for_absolute(page_url: str) -> str:
    # ensure_absolute_url only joins absolute for leading "/" paths;
    # provide an origin-like base.
    from urllib.parse import urlparse

    p = urlparse(page_url)
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}"

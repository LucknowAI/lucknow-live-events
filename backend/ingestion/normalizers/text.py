from __future__ import annotations

import re

_HTML_TAG = re.compile(r"<[^>]+>")
_MULTI_WS = re.compile(r"\s{2,}")
_TRACKING_PARAMS = re.compile(r"[?&](utm_[^&=]+=|fbclid=|gclid=)[^&]*", re.IGNORECASE)

MAX_DESCRIPTION_CHARS = 2000
MAX_EXTRACTION_CHARS = 8000


def strip_html(text: str) -> str:
    return _HTML_TAG.sub(" ", text)


def collapse_whitespace(text: str) -> str:
    return _MULTI_WS.sub(" ", text).strip()


def clean_text(text: str, max_chars: int = MAX_DESCRIPTION_CHARS) -> str:
    text = strip_html(text)
    text = collapse_whitespace(text)
    return text[:max_chars]


def strip_tracking_params(url: str) -> str:
    return _TRACKING_PARAMS.sub("", url).rstrip("?&")


def ensure_absolute_url(url: str, base_url: str = "") -> str:
    if not url:
        return url
    url = url.strip()
    if url.startswith("http://") or url.startswith("https://"):
        return strip_tracking_params(url)
    if url.startswith("//"):
        return strip_tracking_params(f"https:{url}")
    if base_url and url.startswith("/"):
        return strip_tracking_params(f"{base_url.rstrip('/')}{url}")
    return url

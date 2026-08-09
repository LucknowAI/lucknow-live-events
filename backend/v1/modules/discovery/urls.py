"""URL normalization — the function that decides two URLs are the same page.

Everything downstream rests on this. `discovered_url` is unique on `sha256(normalized)`,
the novelty floor counts "URLs we have never seen", and Phase 6's dedup uses normalized-URL
equality as its single strongest signal. If the normalizer is loose, the engine re-fetches
and re-extracts the same page under three spellings and pays three times; if it is
aggressive, two genuinely different events collapse into one.

What is normalized, and why each one is safe:

| Step | Reason it cannot change which page is meant |
|---|---|
| Lowercase scheme and host | Both are case-insensitive by RFC 3986 |
| Drop the default port (`:80`/`:443`) | Same origin by definition |
| Strip a leading `www.` | Universally an alias in practice |
| Drop the fragment | Never sent to the server |
| Drop tracking parameters | Analytics, not content — see `TRACKING_PARAMS` |
| Sort remaining query parameters | Order is not significant to any router we target |
| Collapse a trailing slash | `…/event/` and `…/event` are the same page everywhere we look |
| Percent-encoding normalized by `urlsplit`/`urlunsplit` round-trip | Lossless |

The **path case is deliberately preserved.** Paths are case-sensitive on most servers, and
event slugs are exactly where a lowercase-everything normalizer silently merges two pages.
"""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from v1.platform.net import host_of as _host_of

TRACKING_PARAMS = frozenset(
    {
        # Analytics campaign tags.
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_source_platform",
        # Ad-network click identifiers.
        "gclid",
        "gclsrc",
        "dclid",
        "fbclid",
        "msclkid",
        "twclid",
        "igshid",
        "ttclid",
        # Mail and referral tags.
        "mc_cid",
        "mc_eid",
        "ref",
        "referrer",
        "source",
        "_hsenc",
        "_hsmi",
        "yclid",
        "vero_id",
        "spm",
    }
)

DEFAULT_PORTS = {"http": "80", "https": "443"}


def normalize_url(url: str) -> str:
    """Return the canonical form of `url`. Raises `ValueError` for something unusable."""
    candidate = (url or "").strip()
    if not candidate:
        raise ValueError("empty URL")

    parts = urlsplit(candidate)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"unsupported scheme {parts.scheme!r}")

    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("URL has no host")
    host = host.removeprefix("www.")

    netloc = host
    port = parts.port
    if port is not None and str(port) != DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs), doseq=True)

    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(normalized_url: str) -> str:
    """`sha256` of the **normalized** form.

    Hashing after normalization rather than before is what makes the normalizer's decision
    binding: two spellings that normalize the same get one row in `discovered_url`, and no
    later code can accidentally disagree by hashing the raw string instead.
    """
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()


# Re-exported from `platform.net`, which is where it has to live: `providers/` needs the
# same function and `modules/` and `providers/` are sibling layers that may not import each
# other. Imported here so URL handling still reads from one place in this module.
host_of = _host_of


def host_matches(host: str, pattern: str) -> bool:
    """True when `host` is `pattern` or a subdomain of it.

    Substring matching would be wrong in a way that matters: `"facebook.com" in
    "notfacebook.com.evil.example"` is True, and that is how a blocklist becomes a
    permit-list for anyone who buys the right domain.
    """
    host = host.lower().lstrip(".")
    pattern = pattern.lower().lstrip(".")
    return host == pattern or host.endswith(f".{pattern}")

"""Network-address safety checks shared by everything that fetches a third-party URL.

This is the **enumeration-time subset** of the SSRF guard. Phase 3's fetch module owns the
full version — DNS resolution with a rebind-resistant connect, per-tier response caps, and
robots handling. What is here is the part that must exist *now*, because Phase 2 already
follows URLs a third party controls: a sitemap index tells us which sitemaps to fetch next,
and a sitemap is a list of URLs written by someone else.

Three checks, in the order they catch things:

1. **Scheme allowlist.** `file://`, `gopher://` and friends are never fetchable.
2. **Host-form allowlist.** A host must be *either* a strict IP literal *or* a plausible
   DNS name. Anything else is refused — see below, this is the check that matters most.
3. **Range rejection.** A literal address is checked against loopback, private,
   link-local, reserved, multicast and carrier-grade NAT.

## Why the host-form check exists

The obvious implementation — "try to parse it as an IP; if that fails it must be a
hostname" — is wrong, and it fails open. Verified on this machine:

    ipaddress.ip_address("2130706433")  -> ValueError    but  resolves to 127.0.0.1
    ipaddress.ip_address("0177.0.0.1")  -> ValueError    but  resolves to 127.0.0.1
    ipaddress.ip_address("127.1")       -> ValueError    but  resolves to 127.0.0.1
    ipaddress.ip_address("0x7f000001")  -> ValueError    but  resolves to 127.0.0.1

Python's `ipaddress` module is deliberately strict: it only accepts dotted-quad with no
leading zeros. The C resolver `getaddrinfo` is not — it accepts the whole legacy
`inet_aton` family: 32-bit integers, octal, hex, and short forms. So every one of those
four strings falls through a naive check as "just a hostname" and then resolves straight
to loopback.

Enumerating those encodings and normalising them is a losing game. Instead this refuses
any host that is **neither** a strict IP literal **nor** a plausible DNS name, where
"plausible DNS name" requires the final label to contain a letter — which every real TLD
does and no numeric encoding does. One rule, and it closes the whole family at once
including encodings nobody has thought of yet.

## What this deliberately does NOT do

It does not resolve DNS. A name-based check here would be a TOCTOU illusion: the resolver
could hand us a public address and hand the HTTP client a private one moments later. Doing
that properly means controlling the socket, which is Phase 3's job. Claiming it now would
be worse than not claiming it, so `fetch/guard.py` supersedes this file.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

DEFAULT_ALLOWED_SCHEMES = frozenset({"http", "https"})

BLOCKED_HOST_LITERALS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
        "instance-data",
    }
)

CARRIER_GRADE_NAT = ipaddress.ip_network("100.64.0.0/10")
"""RFC 6598. Not flagged private by `ipaddress`, but it is not routable on the public
internet either, and it is where a cloud provider's internal services often live."""

_HOSTNAME_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_HAS_LETTER = re.compile(r"[a-z]")


class UnsafeUrl(ValueError):
    """The URL is one we refuse to fetch. Raised, never silently skipped."""


def _blocked_reason(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """Why this literal address is not fetchable, or None if it is fine."""
    # An IPv4-mapped IPv6 address (`::ffff:127.0.0.1`) is not loopback *as an IPv6
    # address* — the flags describe ::1. Unwrap it and judge the address it really means.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return _blocked_reason(mapped)
    for tunnelled in ("sixtofour", "teredo"):
        embedded = getattr(address, tunnelled, None)
        if embedded is not None:
            inner = embedded[0] if isinstance(embedded, tuple) else embedded
            reason = _blocked_reason(inner)
            if reason is not None:
                return f"{reason} (via {tunnelled})"

    if address.is_loopback:
        return "loopback address"
    if address.is_link_local:
        # Covers 169.254.169.254, the cloud metadata endpoint.
        return "link-local address"
    if address.is_private:
        return "private address"
    if address.is_reserved:
        return "reserved address"
    if address.is_multicast:
        return "multicast address"
    if address.is_unspecified:
        return "unspecified address"
    if address.version == 4 and address in CARRIER_GRADE_NAT:
        return "carrier-grade NAT address"
    return None


def _looks_like_dns_name(host: str) -> bool:
    """True for something a public DNS name could plausibly be.

    Two requirements, and both are load-bearing against the legacy-encoding family:

    * **At least one dot.** A single-label host is never a public name; `2130706433` and
      `0x7f000001` are single-label, and so is every intranet short name, which we also
      have no business fetching.
    * **A letter in the final label.** Every real TLD has one. `127.1` and `0177.0.0.1`
      end in `1`.

    The dot requirement is what catches the hexadecimal form — `0x7f000001` does contain
    letters, so a letter check alone passes it. Between the two, all four documented
    encodings are refused without enumerating any of them.
    """
    host = host.rstrip(".")
    if not host or len(host) > 253:
        return False
    labels = host.split(".")
    if len(labels) < 2:
        return False
    if any(not _HOSTNAME_LABEL.match(label) for label in labels):
        return False
    return bool(_HAS_LETTER.search(labels[-1]))


def assert_fetchable(url: str, *, allowed_schemes: frozenset[str] | set[str] | None = None) -> str:
    """Return `url` if it is safe to fetch, else raise `UnsafeUrl` naming the reason."""
    schemes = frozenset(allowed_schemes or DEFAULT_ALLOWED_SCHEMES)
    parts = urlsplit(url.strip())

    if parts.scheme.lower() not in schemes:
        raise UnsafeUrl(f"scheme {parts.scheme!r} is not fetchable (allowed: {sorted(schemes)})")

    try:
        host = (parts.hostname or "").lower()
    except ValueError as exc:
        # `hostname` raises on a malformed IPv6 literal rather than returning None.
        raise UnsafeUrl(f"URL has an unparseable host: {exc}") from exc

    if not host:
        raise UnsafeUrl("URL has no host")

    if host in BLOCKED_HOST_LITERALS:
        raise UnsafeUrl(f"host {host!r} is a local or metadata address")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Not a strict IP literal. It must then look like a DNS name — see the module
        # docstring for why "anything that is not an IP is a hostname" fails open.
        if not _looks_like_dns_name(host):
            raise UnsafeUrl(
                f"host {host!r} is neither a valid IP literal nor a plausible DNS name. "
                "Legacy numeric forms (integer, octal, hex, short-form) are refused "
                "because the system resolver accepts them and they commonly encode "
                "loopback"
            ) from None
        return url

    reason = _blocked_reason(address)
    if reason is not None:
        raise UnsafeUrl(f"host {host!r} is a {reason}")
    return url


def host_of(url: str) -> str:
    """Lowercase host without a leading `www.`, or `""` if there isn't one.

    Lives here rather than in `modules/discovery/urls.py` because `providers/` needs it
    too and the two are sibling layers that may not import each other. The discovery
    module re-exports it so URL handling still reads from one place there.
    """
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def is_fetchable(url: str, *, allowed_schemes: frozenset[str] | set[str] | None = None) -> bool:
    """Boolean form, for filtering a list where one bad entry must not abort the batch."""
    try:
        assert_fetchable(url, allowed_schemes=allowed_schemes)
    except UnsafeUrl:
        return False
    return True

"""The source-enumeration port.

Interface only — imports `contracts` and nothing else.

**Why `SourceEnumerator` and not the plan's `SourceAdapter`.** The parent plan's
`SourceAdapter` has both `discover()` and `fetch(url)`. Only the enumeration half exists
in this phase; naming a half-built interface after the finished one invites a caller to
assume `fetch()` is on its way. Phase 3 either adds `SourceAdapter` beside this or widens
this one deliberately — either way it will be a decision, not an accident.

An enumerator answers one question: *which candidate items does this known source have
right now?* It does not fetch individual pages, follow links, or extract fields.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from v1.contracts.discovery import DiscoveredItem
from v1.contracts.source import SourceKind, SourceTier


@runtime_checkable
class SourceEnumerator(Protocol):
    """One configured, known source that can list its own items without a search query."""

    source_id: str
    """The config source id, e.g. a chapter or a feed. Used in logs and accounting."""

    adapter: str
    """Which adapter implementation this is, e.g. `bevy_api`, `sitemap`."""

    tier: SourceTier
    kind: SourceKind
    """`STRUCTURED` when the payload is complete enough to skip extraction entirely."""

    async def enumerate_items(self) -> list[DiscoveredItem]:
        """List the source's current items.

        Raises:
            v1.contracts.errors.SourceEnumerationFailed: the source could not be read, or
                returned a result the adapter can prove is implausible (an unfiltered
                firehose where a filtered page was requested). Never a partial list
                presented as complete.
            v1.contracts.errors.ProviderError: transport, auth or timeout.
        """
        ...

    async def aclose(self) -> None:
        """Release transport resources. Safe to call more than once."""
        ...

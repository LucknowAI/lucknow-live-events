"""Config source id → enumerator instance.

The only place that knows which adapter class implements which config `adapter:` value.
Everything else asks for a source by id and gets something satisfying the port.

Construction is eager and validated at startup — an unknown adapter name or a `bevy_api`
source with no chapter id fails on boot, not halfway through the first run of the night —
but HTTP clients are lazy, so building the registry costs nothing and opens no sockets.
"""

from __future__ import annotations

from v1.config.loader import LoadedConfig
from v1.config.schema import SourceConfig
from v1.contracts.errors import ConfigError
from v1.contracts.source import SourceTier
from v1.platform.logging import get_logger
from v1.providers.sources.base import DEFAULT_USER_AGENT, BaseSourceEnumerator
from v1.providers.sources.bevy_api import BevyApiEnumerator
from v1.providers.sources.sitemap import SitemapEnumerator

logger = get_logger("v1.sources.registry")

ENUMERATORS: dict[str, type[BaseSourceEnumerator]] = {
    BevyApiEnumerator.adapter: BevyApiEnumerator,
    SitemapEnumerator.adapter: SitemapEnumerator,
}

_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "bevy_api": ("base_url", "external_ref"),
    "sitemap": ("url",),
}


class SourceRegistry:
    """All configured focused sources, built once at startup."""

    def __init__(self, *, loaded: LoadedConfig, user_agent: str = DEFAULT_USER_AGENT) -> None:
        self._loaded = loaded
        self._user_agent = user_agent
        self._enumerators: dict[str, BaseSourceEnumerator] = {}
        self._build()

    def _build(self) -> None:
        discovery = self._loaded.config.discovery
        sources = discovery.sources if discovery is not None else []

        for source in sources:
            _validate_source(source)
            if not source.enabled:
                continue
            adapter_cls = ENUMERATORS[source.adapter]
            self._enumerators[source.id] = adapter_cls(source=source, user_agent=self._user_agent)

        logger.info(
            "source_registry_ready",
            sources=sorted(self._enumerators),
            disabled=[s.id for s in sources if not s.enabled],
            adapters=sorted(ENUMERATORS),
        )

    # ------------------------------------------------------------------------ lookup

    @property
    def source_ids(self) -> list[str]:
        return sorted(self._enumerators)

    def get(self, source_id: str) -> BaseSourceEnumerator:
        enumerator = self._enumerators.get(source_id)
        if enumerator is None:
            raise ConfigError(
                f"unknown or disabled discovery source {source_id!r}; enabled: {self.source_ids}"
            )
        return enumerator

    def all(self) -> list[BaseSourceEnumerator]:
        """Enabled sources, cheapest tier first — T0 before T1, so the structured, free
        path always runs before the URL-only one."""
        return sorted(
            self._enumerators.values(),
            key=lambda enumerator: (_TIER_ORDER[enumerator.tier], enumerator.source_id),
        )

    async def aclose(self) -> None:
        for enumerator in self._enumerators.values():
            await enumerator.aclose()


_TIER_ORDER: dict[SourceTier, int] = {
    SourceTier.T0_API: 0,
    SourceTier.T1_FEED: 1,
    SourceTier.T2_STATIC: 2,
    SourceTier.T3_BROWSER: 3,
}


def _validate_source(source: SourceConfig) -> None:
    """Startup validation that needs the adapter registry, so it cannot live in the schema.

    The schema knows a source has `url` or `external_ref`; only the registry knows that a
    `bevy_api` source without `external_ref` can never work.
    """
    if source.adapter not in ENUMERATORS:
        raise ConfigError(
            f"discovery source {source.id!r} uses unknown adapter {source.adapter!r}; "
            f"known adapters: {sorted(ENUMERATORS)}"
        )
    missing = [
        field for field in _REQUIRED_FIELDS[source.adapter] if not getattr(source, field, None)
    ]
    if missing:
        raise ConfigError(
            f"discovery source {source.id!r} ({source.adapter}) is missing required "
            f"field(s): {', '.join(missing)}"
        )

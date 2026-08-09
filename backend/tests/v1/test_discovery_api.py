"""Config validation for the discovery sections, and the endpoints end to end.

The config tests are the important half. Every one of them is a mistake that would
otherwise be discovered by a bill, a silently empty result set, or a 3am page.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from v1.api.app import create_app
from v1.config.loader import LoadedConfig, load_config
from v1.config.settings import V1Settings
from v1.contracts.discovery import DiscoveredItem, StrategyKind
from v1.contracts.errors import ConfigError
from v1.contracts.source import SourceKind, SourceTier
from v1.modules.discovery.store import InMemoryDiscoveryStore
from v1.providers.search.registry import SearchRegistry
from v1.providers.sources.registry import SourceRegistry

# ---------------------------------------------------------------- config validation


def _load(settings: V1Settings, path: Any, resolver: Any) -> LoadedConfig:
    return load_config(path, settings=settings, resolver=resolver)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        # A chain entry nobody configured is a run that quietly does nothing.
        ({"search": {"chain": ["ghost"]}}, "not a configured provider"),
        ({"search": {"chain": ["primary", "primary"]}}, "duplicate provider"),
        # A cap that cannot price a query is decoration.
        (
            {"search": {"providers": {"primary": {"pricing": None}}}},
            "must declare `pricing`",
        ),
        # A pasted key in a *_ref field is a secret in a committed file.
        (
            {"search": {"providers": {"primary": {"api_key_ref": "sk-live-abc123"}}}},
            "UPPER_SNAKE environment variable name",
        ),
        # The ~3x price difference between DataForSEO's modes must not live in a default.
        (
            {
                "search": {
                    "providers": {
                        "primary": {
                            "adapter": "dataforseo",
                            "mode": None,
                            "api_key_ref": "TEST_SERPER_KEY",
                        }
                    }
                }
            },
            "must declare `mode`",
        ),
        # Tavily truncates at 20; a silently short page reads as low novelty.
        (
            {"search": {"providers": {"backup": {"max_num": 50}}}},
            "at most 20 results",
        ),
        # The Serper two-credit cliff.
        (
            {"search": {"defaults": {"num": 50}}},
            "allow_deep_pages is false",
        ),
        # Recording a fixture from another fixture records nothing real.
        (
            {"search": {"providers": {"replay": {"record_from": "replay"}}}},
            "cannot reference itself",
        ),
        # A T2/T3 source has nothing to enumerate.
        (
            {"discovery": {"sources": [{"id": "x", "adapter": "sitemap", "tier": "T3"}]}},
            "can only enumerate",
        ),
        # An unusable regex fails at boot, not on the first run.
        (
            {"discovery": {"url_rules": {"event_patterns": ["("]}}},
            "not a valid regex",
        ),
    ],
)
def test_invalid_discovery_config_fails_at_load(
    settings: V1Settings,
    write_discovery_config: Any,
    search_resolver: Any,
    overrides: dict,
    message: str,
) -> None:
    with pytest.raises(ConfigError, match=message):
        _load(settings, write_discovery_config(overrides), search_resolver)


def test_triage_task_without_an_llm_route_is_rejected(
    settings: V1Settings, write_discovery_config: Any, search_resolver: Any
) -> None:
    """Otherwise URL triage silently runs on `llm.default`, which may be the expensive
    model — a fact that would surface on the bill rather than at boot."""
    path = write_discovery_config({"discovery": {"triage": {"enabled": True}}})
    with pytest.raises(ConfigError, match="has no entry in llm.tasks"):
        _load(settings, path, search_resolver)


def test_missing_templates_file_is_fatal(
    settings: V1Settings, write_discovery_config: Any, search_resolver: Any
) -> None:
    """A run that silently issues zero queries looks identical to a run that found nothing."""
    path = write_discovery_config({"discovery": {"templates_path": "nope.yaml"}})
    with pytest.raises(ConfigError, match="config file not found"):
        _load(settings, path, search_resolver)


def test_unknown_source_adapter_fails_at_registry_construction(discovery_config: Any) -> None:
    """The schema knows a source has a `url`; only the registry knows `bevy_api` needs a
    chapter id."""
    broken = discovery_config.config.model_copy(deep=True)
    broken.discovery.sources[0].adapter = "carrier_pigeon"
    loaded = discovery_config.__class__(
        config=broken,
        path=discovery_config.path,
        api_keys={},
        availability={},
        templates=discovery_config.templates,
    )
    with pytest.raises(ConfigError, match="unknown adapter"):
        SourceRegistry(loaded=loaded)


def test_search_registry_is_none_without_a_search_section(loaded_config: LoadedConfig) -> None:
    """A focused-only instance is a legitimate zero-spend deployment, not a broken one."""
    assert SearchRegistry.from_config(loaded_config) is None


def test_chain_skips_providers_with_no_key(
    settings: V1Settings, write_discovery_config: Any, empty_resolver: Any
) -> None:
    """A contributor with one key should get a working chain, not a boot failure."""
    loaded = _load(settings, write_discovery_config(), empty_resolver)
    registry = SearchRegistry.from_config(loaded, settings=settings)
    assert registry is not None
    chain = registry.build_chain()
    # `replay` is the fixture provider and needs no key, so it survives.
    assert chain.provider_names == ["replay"]


def test_chain_with_no_usable_provider_is_an_error(
    settings: V1Settings, write_discovery_config: Any, empty_resolver: Any
) -> None:
    loaded = _load(
        settings,
        write_discovery_config({"search": {"chain": ["primary", "backup"]}}),
        empty_resolver,
    )
    registry = SearchRegistry.from_config(loaded, settings=settings)
    assert registry is not None
    with pytest.raises(ConfigError, match="no usable search provider"):
        registry.build_chain()


def test_fixture_recording_from_a_paid_provider_needs_explicit_consent(
    settings: V1Settings, write_discovery_config: Any, search_resolver: Any
) -> None:
    """`record_from` in a committed config must not turn a fixture-only CI run into a bill."""
    path = write_discovery_config({"search": {"providers": {"replay": {"record_from": "primary"}}}})
    loaded = _load(settings, path, search_resolver)
    registry = SearchRegistry.from_config(loaded, settings=settings)
    assert registry is not None
    replay = registry.get("replay")
    assert replay._record_source is None


# ------------------------------------------------------------------------ endpoints


class StubEnumerator:
    """Satisfies the `SourceEnumerator` port with no network."""

    adapter = "stub"
    kind = SourceKind.STRUCTURED

    def __init__(self, source_id: str, urls: list[str]) -> None:
        self.source_id = source_id
        self.tier = SourceTier.T0_API
        self._urls = urls

    async def enumerate_items(self) -> list[DiscoveredItem]:
        return [
            DiscoveredItem(
                url=url,
                raw_url=url,
                strategy=StrategyKind.FOCUSED,
                origin=self.source_id,
                tier=self.tier,
                structured={"id": index},
            )
            for index, url in enumerate(self._urls)
        ]

    async def aclose(self) -> None:
        return


@pytest.fixture
def client(
    settings: V1Settings, write_discovery_config: Any, search_resolver: Any, monkeypatch: Any
) -> TestClient:
    path = write_discovery_config()
    monkeypatch.setenv("V1_CONFIG_PATH", str(path))
    monkeypatch.setenv("V1_ENVIRONMENT", "development")

    from v1.config.settings import get_settings

    get_settings.cache_clear()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_templates_endpoint_renders_for_today(client: TestClient) -> None:
    body = client.get("/discovery/templates").json()
    assert body["context"]["city_keywords"] == '"Testville"'
    alpha = next(t for t in body["templates"] if t["id"] == "alpha")
    assert alpha["render_error"] is None
    assert '"Testville"' in alpha["rendered_query"]
    assert "{city_keywords}" not in alpha["rendered_query"]


def test_providers_endpoint_separates_policy_from_live_verdict(client: TestClient) -> None:
    """Reporting a live state under a policy's field name is the exact defect the LLM
    layer's review caught."""
    body = client.get("/discovery/providers").json()
    assert body["budget_on_exceeded"] == "halt"
    assert body["budget_verdict"] == "allow"
    assert body["chain"] == ["primary", "backup", "replay"]

    tavily = next(p for p in body["providers"] if p["name"] == "backup")
    assert tavily["supports_pagination"] is False
    assert tavily["supports_operators"] is False
    assert tavily["max_num"] == 20


def test_sources_endpoint_lists_configured_sources(client: TestClient) -> None:
    body = client.get("/discovery/sources").json()
    assert [source["id"] for source in body] == ["demo_chapter"]
    assert body[0]["tier"] == "T0"


def test_preview_writes_nothing(client: TestClient, monkeypatch: Any) -> None:
    """The tuning tool must not mutate the state it is tuning."""
    stub = StubEnumerator(
        "demo_chapter",
        [
            "https://gdg.community.dev/events/details/one/?utm_source=x",
            "https://gdg.community.dev/events/?page=2",
            "https://facebook.com/events/9",
        ],
    )
    client.app.state.source_registry._enumerators = {"demo_chapter": stub}

    recorded: list[list[DiscoveredItem]] = []
    original = InMemoryDiscoveryStore.record_items

    async def _spy(self: InMemoryDiscoveryStore, items: list[DiscoveredItem]) -> int:
        recorded.append(items)
        return await original(self, items)

    monkeypatch.setattr(InMemoryDiscoveryStore, "record_items", _spy)

    body = client.post("/discovery/preview", json={"strategy": "focused"}).json()

    assert body["dry_run"] is True
    assert recorded == []  # nothing persisted, not even to the in-memory store
    verdicts = {item["verdict"] for item in body["items"]}
    assert verdicts == {"event_page", "listing"}
    # The blocked host never becomes a candidate at all.
    assert all("facebook" not in item["url"] for item in body["items"])
    # Tracking parameters are gone by the time a URL is a candidate.
    assert body["items"][0]["url"] == "https://gdg.community.dev/events/details/one"


def test_run_without_a_database_degrades_loudly_rather_than_crashing(client: TestClient) -> None:
    client.app.state.source_registry._enumerators = {
        "demo_chapter": StubEnumerator("demo_chapter", ["https://x.example.com/events/details/a/"])
    }
    body = client.post("/discovery/run", json={"strategy": "focused"}).json()
    assert body["dry_run"] is False
    assert len(body["items"]) == 1


def test_one_failing_template_does_not_abort_the_run(client: TestClient) -> None:
    """With no SERP keys the chain falls through to the fixture provider, which has no
    recording for these queries. That is a loud, correct failure — but it must land on the
    report per template, not take the other nine templates with it."""
    client.app.state.source_registry._enumerators = {}

    response = client.post("/discovery/preview", json={"strategy": "general", "tiers": ["A", "B"]})
    assert response.status_code == 200

    body = response.json()
    assert body["items"] == []
    # Both templates were attempted and both reported their own reason.
    assert {outcome["template_id"] for outcome in body["templates"]} == {"alpha", "beta"}
    assert all(
        outcome["stopped_because"].startswith("provider_error: fixture_missing")
        for outcome in body["templates"]
    )


def test_every_discovery_endpoint_requires_the_token(client) -> None:
    """The reads look harmless and are not: `templates` is the exact query text sent to a
    paid API, `state` shows which queries pay off, `providers` reports spend."""
    from v1.api.deps import TOKEN_HEADER

    client.app.state.settings.ENGINE_TOKEN = "secret-token"
    try:
        for path in ("/discovery/providers", "/discovery/sources", "/discovery/templates"):
            assert client.get(path).status_code == 401, path
            assert client.get(path, headers={TOKEN_HEADER: "secret-token"}).status_code == 200
        # Liveness stays open — a health check that needs a secret is a health check
        # nobody wires up.
        assert client.get("/health").status_code == 200
    finally:
        client.app.state.settings.ENGINE_TOKEN = None

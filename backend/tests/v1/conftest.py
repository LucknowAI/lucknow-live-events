"""Shared fixtures for the V1 engine tests.

Two rules these fixtures exist to enforce:

* **No test reads the developer's environment.** Every `V1Settings` is built with
  `_env_file=None` and explicit values, so a stray `GEMINI_API_KEY` in a shell cannot make a
  test pass (or a live call happen) that would fail in CI.
* **No test touches the network or the database.** Only the `mock`, `fixture` and stub
  adapters are used. Live-provider runs are a separate, opt-in path (`-m live`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from v1.config.loader import LoadedConfig, SecretResolver, load_config
from v1.config.settings import V1Settings
from v1.platform.budget import InMemorySpendLedger
from v1.providers.llm.registry import LLMRegistry

BASE_CONFIG: dict[str, Any] = {
    "config_version": 1,
    "tenant": {"slug": "testville", "name": "Testville Events", "timezone": "Asia/Kolkata"},
    "llm": {
        "default": "mock_default",
        "budget": {"daily_usd_cap": 1.00, "on_exceeded": "halt", "ledger": "memory"},
        "tasks": {"extraction": "mock_default"},
        "profiles": {
            "mock_default": {"adapter": "mock", "structured_output": "native_schema"},
            "fixtures": {
                "adapter": "fixture",
                "structured_output": "native_schema",
                "fixture_dir": "tests/v1/fixtures/llm",
            },
            "paid_gemini": {
                "adapter": "gemini_native",
                "model": "gemini-3.1-flash-lite",
                "api_key_ref": "TEST_GEMINI_KEY",
                "structured_output": "native_schema",
                "pricing": {
                    "input_usd_per_mtok": 0.25,
                    "output_usd_per_mtok": 1.50,
                    "source": "test",
                    "as_of": "2026-08-06",
                },
            },
        },
    },
}


def deep_merge(base: dict, overrides: dict) -> dict:
    """Recursive dict merge; `None` as an override value deletes the key."""
    result = {
        key: (deep_merge(value, {}) if isinstance(value, dict) else value)
        for key, value in base.items()
    }
    for key, value in overrides.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@pytest.fixture
def settings(tmp_path: Path) -> V1Settings:
    return V1Settings(
        _env_file=None,
        CONFIG_PATH=tmp_path / "instance.yaml",
        ENVIRONMENT="development",
        SECRETS_DIR=tmp_path / "secrets",
        LOG_FORMAT="console",
        DATABASE_URL=None,
        ENGINE_TOKEN=None,
        LLM_ALLOW_LIVE=False,
    )


@pytest.fixture
def write_config(tmp_path: Path) -> Any:
    """Write a config file derived from `BASE_CONFIG` and return its path."""

    def _write(overrides: dict | None = None, *, name: str = "instance.yaml") -> Path:
        payload = deep_merge(BASE_CONFIG, overrides or {})
        path = tmp_path / name
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def empty_resolver(tmp_path: Path) -> SecretResolver:
    """A resolver that finds nothing — no ambient key can leak into a test."""
    return SecretResolver(env_prefix="NO_SUCH_PREFIX_", secrets_dir=tmp_path / "nowhere")


@pytest.fixture
def keyed_resolver(tmp_path: Path) -> SecretResolver:
    """A resolver backed by a file-mounted secret, the Cloud Run shape."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(exist_ok=True)
    (secrets_dir / "TEST_GEMINI_KEY").write_text("test-key-value\n", encoding="utf-8")
    return SecretResolver(env_prefix="NO_SUCH_PREFIX_", secrets_dir=secrets_dir)


@pytest.fixture
def loaded_config(
    settings: V1Settings, write_config: Any, keyed_resolver: SecretResolver
) -> LoadedConfig:
    return load_config(write_config(), settings=settings, resolver=keyed_resolver)


@pytest.fixture
def ledger() -> InMemorySpendLedger:
    return InMemorySpendLedger()


@pytest.fixture
def registry(loaded_config: LoadedConfig, ledger: InMemorySpendLedger) -> LLMRegistry:
    # No cleanup needed: only mock/fixture profiles are exercised here and the network
    # adapters build their clients lazily, so nothing is opened.
    return LLMRegistry.from_config(loaded_config, ledger=ledger)


# ---------------------------------------------------------------- discovery (Phase 2)

SEARCH_CONFIG: dict[str, Any] = {
    "chain": ["primary", "backup", "replay"],
    "budget": {"monthly_usd_cap": 5.00, "on_exceeded": "halt", "ledger": "memory"},
    "cache": {"ttl_hours": 6, "backend": "memory"},
    "defaults": {"num": 10, "gl": "in", "hl": "en"},
    "allow_deep_pages": False,
    "failover": {"consecutive_failures": 2, "cooldown_minutes": 30},
    "providers": {
        "primary": {
            "adapter": "serper",
            "api_key_ref": "TEST_SERPER_KEY",
            "pricing": {
                "usd_per_query": 0.001,
                "deep_query_multiplier": 2,
                "source": "test",
                "as_of": "2026-08-08",
            },
        },
        "backup": {
            "adapter": "tavily",
            "api_key_ref": "TEST_TAVILY_KEY",
            "pricing": {"usd_per_query": 0.008, "source": "test", "as_of": "2026-08-08"},
        },
        "replay": {"adapter": "fixture", "fixture_dir": "tests/v1/fixtures/serp"},
    },
}

DISCOVERY_CONFIG: dict[str, Any] = {
    "novelty_floor": 0.15,
    "templates_path": "templates.yaml",
    "triage": {"enabled": False, "task": "url_triage", "batch_size": 5, "max_batches": 2},
    "url_rules": {
        "blocked_hosts": ["facebook.com", "linkedin.com"],
        "listing_patterns": [r"/events/?$", r"[?&]page=\d+"],
        "event_patterns": [r"/events/details/", r"/events/[^/]+$"],
    },
    "sources": [
        {
            "id": "demo_chapter",
            "adapter": "bevy_api",
            "tier": "T0",
            "base_url": "https://events.example.com",
            "external_ref": "42",
            "unfiltered_sentinel": 5000,
        }
    ],
}

TEMPLATES_FILE: dict[str, Any] = {
    "templates": [
        {
            "id": "alpha",
            "tier": "A",
            "query": "site:example.com inurl:/events/details/ ({city_keywords}) after:{date_floor}",
            "max_pages": 3,
        },
        {"id": "beta", "tier": "B", "query": "({community_names}) register", "max_pages": 2},
    ]
}

LOCALITY_CONFIG: dict[str, Any] = {
    "city_keywords": ["Testville"],
    "community_names": ["Testville Devs"],
    "institution_names": ["Testville Institute"],
}


@pytest.fixture
def write_discovery_config(tmp_path: Path, write_config: Any) -> Any:
    """An instance config with `locality`, `search` and `discovery`, plus templates.yaml.

    Kept separate from `write_config` so the Phase 1 tests keep exercising a config that
    has no search section at all — which is a supported, zero-spend deployment shape and
    would otherwise stop being tested.
    """

    def _write(overrides: dict | None = None, templates: dict | None = None) -> Path:
        (tmp_path / "templates.yaml").write_text(
            yaml.safe_dump(templates or TEMPLATES_FILE, sort_keys=False), encoding="utf-8"
        )
        base = {
            "locality": LOCALITY_CONFIG,
            "search": SEARCH_CONFIG,
            "discovery": DISCOVERY_CONFIG,
        }
        return write_config(deep_merge(base, overrides or {}))

    return _write


@pytest.fixture
def search_resolver(tmp_path: Path) -> SecretResolver:
    """Resolves the test SERP keys from a mounted-file secrets dir."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(exist_ok=True)
    (secrets_dir / "TEST_GEMINI_KEY").write_text("test-key-value\n", encoding="utf-8")
    (secrets_dir / "TEST_SERPER_KEY").write_text("serper-test-key\n", encoding="utf-8")
    (secrets_dir / "TEST_TAVILY_KEY").write_text("tavily-test-key\n", encoding="utf-8")
    return SecretResolver(env_prefix="NO_SUCH_PREFIX_", secrets_dir=secrets_dir)


@pytest.fixture
def discovery_config(
    settings: V1Settings, write_discovery_config: Any, search_resolver: SecretResolver
) -> LoadedConfig:
    return load_config(write_discovery_config(), settings=settings, resolver=search_resolver)

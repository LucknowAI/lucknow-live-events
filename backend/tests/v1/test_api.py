"""The HTTP surface: the gate, the guard rails, and the endpoints as debugging tools.

The app is built with a real lifespan against a temporary config, so these tests also cover
the startup checks — the ones that must refuse to boot rather than boot something expensive.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from v1.api.app import create_app, validate_runtime
from v1.config.loader import load_config
from v1.config.settings import V1Settings
from v1.contracts.errors import ConfigError


def client_for(path: Path, **settings_overrides) -> TestClient:
    """A TestClient whose app state is built from an explicit settings object.

    The lifespan normally reads the process environment; here it is patched to the settings
    the test wants, so no developer env var can influence the result.
    """
    settings = V1Settings(
        _env_file=None,
        CONFIG_PATH=path,
        SECRETS_DIR=path.parent / "secrets",
        ENVIRONMENT="development",
        **settings_overrides,
    )
    app = create_app()

    import v1.api.app as app_module

    original = app_module.get_settings
    app_module.get_settings = lambda: settings  # type: ignore[assignment]
    try:
        client = TestClient(app)
        client.__enter__()
    finally:
        app_module.get_settings = original  # type: ignore[assignment]
    return client


@pytest.fixture
def api(write_config, keyed_resolver) -> TestClient:
    path = write_config()
    # The keyed_resolver fixture creates the secrets dir the settings above point at.
    assert keyed_resolver
    client = client_for(path)
    yield client
    client.__exit__(None, None, None)


def test_health_reports_config_and_profile_state(api: TestClient) -> None:
    response = api.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["tenant"] == "testville"
    assert body["default_profile"] == "mock_default"
    assert body["profiles_total"] == 3
    assert response.headers["X-Correlation-Id"]


def test_correlation_id_is_echoed_when_supplied(api: TestClient) -> None:
    response = api.get("/health", headers={"X-Correlation-Id": "trace-abc-123"})
    assert response.headers["X-Correlation-Id"] == "trace-abc-123"


def test_profiles_lists_capabilities_pricing_and_spend(api: TestClient) -> None:
    body = api.get("/llm/profiles").json()
    assert body["default"] == "mock_default"
    assert "ExtractedEvent" in body["schemas"]
    assert body["budget_cap_usd"] == "1.0"
    assert body["budget_spent_usd"] == "0"

    profiles = {profile["name"]: profile for profile in body["profiles"]}
    assert profiles["paid_gemini"]["is_paid"] is True
    assert profiles["paid_gemini"]["pricing_as_of"] == "2026-08-06"
    assert profiles["mock_default"]["enforces_schema"] is True


def test_budget_policy_and_live_verdict_are_separate_fields(api: TestClient) -> None:
    """`on_exceeded` is the configured policy; `verdict` is the current state.

    Reporting the verdict under the policy's name is how an operator reads "halt" and
    concludes spending is blocked when nothing has been spent at all.
    """
    body = api.get("/llm/profiles").json()
    assert body["budget_on_exceeded"] == "halt"  # policy, from instance.yaml
    assert body["budget_verdict"] == "allow"  # state: nothing spent yet
    assert body["budget_remaining_usd"] == "1.0"
    assert body["budget_ledger"] == "memory"


def test_complete_runs_through_the_mock_profile(api: TestClient) -> None:
    response = api.post(
        "/llm/complete",
        json={
            "system": "Extract facts.",
            "user": "Report the fields.",
            "schema_name": "LlmSelfTestProbe",
            "task": "extraction",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"] == "mock_default"
    assert body["outcome"] == "ok"
    assert body["cost_usd"] == "0"
    assert set(body["value"]) == {"title", "city", "year", "is_free"}


def test_unknown_schema_name_is_a_400_not_a_500(api: TestClient) -> None:
    response = api.post(
        "/llm/complete",
        json={"system": "s", "user": "u", "schema_name": "WhateverIWant"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "schema_not_registered"


def test_paid_profile_is_refused_unless_live_spend_is_enabled(api: TestClient) -> None:
    response = api.post(
        "/llm/complete",
        json={
            "system": "s",
            "user": "u",
            "schema_name": "LlmSelfTestProbe",
            "profile": "paid_gemini",
        },
    )
    assert response.status_code == 403
    assert "V1_LLM_ALLOW_LIVE" in response.json()["detail"]


def test_selftest_reports_per_check_results(api: TestClient) -> None:
    body = api.post("/llm/selftest", json={"profile": "mock_default"}).json()
    names = {check["name"] for check in body["checks"]}
    assert names == {
        "structured_output",
        "injection_resistance",
        "timeout_is_typed",
        "budget_cap_halts",
    }
    assert {check["status"] for check in body["checks"]} <= {"pass", "fail", "skip"}


def test_config_endpoint_is_redacted(api: TestClient) -> None:
    body = api.get("/config").json()
    assert "test-key-value" not in json.dumps(body)
    assert body["tenant"]["slug"] == "testville"


# ---------------------------------------------------------------------- token gating


def test_gated_endpoints_require_the_token_when_one_is_configured(
    write_config, keyed_resolver
) -> None:
    assert keyed_resolver
    path = write_config()
    client = client_for(path, ENGINE_TOKEN="s3cret")
    try:
        assert client.get("/health").status_code == 200  # ungated
        assert client.get("/llm/profiles").status_code == 401
        assert client.get("/llm/profiles", headers={"X-Engine-Token": "wrong"}).status_code == 401
        ok = client.get("/llm/profiles", headers={"X-Engine-Token": "s3cret"})
        assert ok.status_code == 200
    finally:
        client.__exit__(None, None, None)


# ------------------------------------------------------------------- startup guards


def test_production_without_a_token_refuses_to_start(
    write_config, keyed_resolver, tmp_path
) -> None:
    """These endpoints spend money; an unauthenticated deployed one is a funded prompt relay."""
    path = write_config()
    settings = V1Settings(
        _env_file=None,
        CONFIG_PATH=path,
        SECRETS_DIR=tmp_path / "secrets",
        ENVIRONMENT="production",
        ENGINE_TOKEN=None,
    )
    config = load_config(path, settings=settings, resolver=keyed_resolver)
    with pytest.raises(ConfigError) as exc:
        validate_runtime(settings, config)
    assert "V1_ENGINE_TOKEN" in str(exc.value)


def test_production_with_a_memory_ledger_refuses_to_start(
    write_config, keyed_resolver, tmp_path
) -> None:
    """A spend cap that a restart clears is not a cap."""
    path = write_config()
    settings = V1Settings(
        _env_file=None,
        CONFIG_PATH=path,
        SECRETS_DIR=tmp_path / "secrets",
        ENVIRONMENT="production",
        ENGINE_TOKEN="s3cret",
    )
    config = load_config(path, settings=settings, resolver=keyed_resolver)
    with pytest.raises(ConfigError) as exc:
        validate_runtime(settings, config)
    assert "memory" in str(exc.value)


def test_development_starts_without_a_token(write_config, keyed_resolver, tmp_path) -> None:
    path = write_config()
    settings = V1Settings(
        _env_file=None,
        CONFIG_PATH=path,
        SECRETS_DIR=tmp_path / "secrets",
        ENVIRONMENT="development",
    )
    config = load_config(path, settings=settings, resolver=keyed_resolver)
    validate_runtime(settings, config)  # warns, does not raise

"""The costless adapters and the conformance suite.

`fixture` and `mock` are not test scaffolding — they are the adapters CI and demos run on,
so they get the same scrutiny as a paid one.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from v1.config.loader import load_config
from v1.config.schema import AdapterKind, LLMProfileConfig, OnBudgetExceeded
from v1.contracts.errors import BudgetExceeded, FixtureMissing
from v1.contracts.event import AttendanceMode, DateSanity, ExtractedEvent
from v1.contracts.llm import CallOutcome, RawCompletion, TokenUsage
from v1.platform.budget import BudgetGuard, InMemorySpendLedger, SpendRecord
from v1.providers.llm.conformance import run_conformance
from v1.providers.llm.fixture import FixtureProvider
from v1.providers.llm.mock import MockProvider, synthesize
from v1.providers.llm.registry import LLMRegistry, build_budget_guard
from v1.providers.llm.schemas import LlmSelfTestProbe, validate_registry

VALID = {"title": "Kubernetes Bootcamp", "city": "Springfield", "year": 2027, "is_free": True}
INVALID = {"title": "Kubernetes Bootcamp", "city": "Springfield", "year": "next year"}


def fixture_provider(directory: Path) -> FixtureProvider:
    return FixtureProvider(
        name="fixtures",
        profile=LLMProfileConfig(adapter=AdapterKind.FIXTURE, fixture_dir=str(directory)),
    )


async def probe(provider, **kwargs):
    return await provider.complete_structured(
        system="Extract facts.",
        user="Report the fields.",
        schema=LlmSelfTestProbe,
        task="selftest",
        **kwargs,
    )


# -------------------------------------------------------------------------- fixture


async def test_missing_fixture_says_exactly_what_to_create(tmp_path: Path) -> None:
    provider = fixture_provider(tmp_path)
    with pytest.raises(FixtureMissing) as exc:
        await probe(provider)

    key = exc.value.context["key"]
    assert key and len(key) == 32
    assert str(tmp_path) in exc.value.context["path"]
    assert "LlmSelfTestProbe" in str(exc.value)


async def test_recorded_fixture_replays_deterministically(tmp_path: Path) -> None:
    provider = fixture_provider(tmp_path)
    with pytest.raises(FixtureMissing) as exc:
        await probe(provider)
    Path(exc.value.context["path"]).write_text(
        json.dumps({"response": VALID, "usage": {"input_tokens": 120, "output_tokens": 30}}),
        encoding="utf-8",
    )

    first = await probe(provider)
    second = await probe(provider)

    assert first.value == second.value
    assert first.value.city == "Springfield"
    assert first.attempts == 1
    assert first.cost_usd == 0  # replay is free by construction
    assert first.usage.input_tokens == 120


async def test_fixture_key_ignores_the_per_call_nonce(tmp_path: Path) -> None:
    """The port randomises the delimiter every call; a nonce-sensitive key would never hit."""
    provider = fixture_provider(tmp_path)
    content = "Kubernetes Bootcamp, Springfield, 2027, free entry."

    with pytest.raises(FixtureMissing) as exc:
        await probe(provider, untrusted_content=content)
    Path(exc.value.context["path"]).write_text(json.dumps({"response": VALID}), encoding="utf-8")

    # A second call generates a different nonce; it must still find the same recording.
    result = await probe(provider, untrusted_content=content)
    assert result.value.year == 2027


async def test_fixture_response_sequence_drives_the_repair_path(tmp_path: Path) -> None:
    """Deterministic coverage of the repair funnel through a real adapter."""
    provider = fixture_provider(tmp_path)
    with pytest.raises(FixtureMissing) as exc:
        await probe(provider)
    Path(exc.value.context["path"]).write_text(
        json.dumps({"responses": [INVALID, VALID]}), encoding="utf-8"
    )

    result = await probe(provider)
    assert result.attempts == 2
    assert result.outcome is CallOutcome.OK_REPAIRED


# ------------------------------------------------------------- fixture recording


async def test_recording_from_a_live_source_is_budgeted_and_billed(tmp_path: Path) -> None:
    """Recording is the one path where a *costless* profile makes a real, paid call.

    Reaching the source adapter directly would skip its budget check and write no ledger row,
    so the recording would be spend the daily cap could never see.
    """
    from tests.v1.test_funnel import StubProvider, paid_profile

    ledger = InMemorySpendLedger()
    guard = BudgetGuard(
        ledger=ledger,
        daily_cap_usd=Decimal("10.00"),
        on_exceeded=OnBudgetExceeded.HALT,
        timezone="UTC",
        cache_ttl_s=0.0,
    )
    source = StubProvider(
        script=[
            RawCompletion(
                text=json.dumps(VALID),
                usage=TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000),
            )
        ],
        name="live_source",
        profile=paid_profile(),
        api_key="k",
        budget=guard,
    )
    replay = FixtureProvider(
        name="fixtures",
        profile=LLMProfileConfig(adapter=AdapterKind.FIXTURE, fixture_dir=str(tmp_path)),
        budget=guard,
        record_from=source,
    )

    result = await probe(replay)
    assert result.value.city == "Springfield"
    assert len(list(tmp_path.glob("*.json"))) == 1  # the recording landed

    # Two rows: the paid source's real spend, and the fixture profile's own free call.
    by_profile = {entry.profile: entry for entry in ledger.entries}
    assert by_profile["live_source"].cost_usd == Decimal("1.750000")
    assert by_profile["live_source"].task == "fixture_record"
    assert by_profile["fixtures"].cost_usd == Decimal(0)


async def test_recording_is_refused_when_the_budget_is_exhausted(tmp_path: Path) -> None:
    from tests.v1.test_funnel import StubProvider, paid_profile

    ledger = InMemorySpendLedger()
    await ledger.record(
        SpendRecord(
            task="extraction",
            profile="live_source",
            adapter="stub",
            model="stub-1",
            schema_name="LlmSelfTestProbe",
            strategy="native_schema",
            tokens_in=0,
            tokens_out=0,
            cost_usd=Decimal("5.00"),
            cost_is_estimated=False,
            latency_ms=0,
            attempts=1,
            outcome=CallOutcome.OK,
        )
    )
    guard = BudgetGuard(
        ledger=ledger,
        daily_cap_usd=Decimal("1.00"),
        on_exceeded=OnBudgetExceeded.HALT,
        timezone="UTC",
        cache_ttl_s=0.0,
    )
    source = StubProvider(
        script=[RawCompletion(text=json.dumps(VALID))],
        name="live_source",
        profile=paid_profile(),
        api_key="k",
        budget=guard,
    )
    replay = FixtureProvider(
        name="fixtures",
        profile=LLMProfileConfig(adapter=AdapterKind.FIXTURE, fixture_dir=str(tmp_path)),
        budget=guard,
        record_from=source,
    )

    with pytest.raises(BudgetExceeded):
        await probe(replay)
    assert source.calls == []
    assert list(tmp_path.glob("*.json")) == []


def test_registry_refuses_to_wire_a_paid_recording_source_without_live_opt_in(
    settings, write_config, keyed_resolver
) -> None:
    """`record_from` in a config file must not be able to turn a fixture run into a bill."""
    path = write_config(
        {
            "llm": {
                "profiles": {
                    "fixtures": {"record_from": "paid_gemini"},
                }
            }
        }
    )
    loaded = load_config(path, settings=settings, resolver=keyed_resolver)

    blocked = LLMRegistry(
        loaded=loaded, budget=build_budget_guard(loaded, InMemorySpendLedger()), allow_live=False
    )
    fixtures = blocked.get("fixtures")
    assert isinstance(fixtures, FixtureProvider)
    assert fixtures._record_from is None

    permitted = LLMRegistry(
        loaded=loaded, budget=build_budget_guard(loaded, InMemorySpendLedger()), allow_live=True
    )
    assert permitted.get("fixtures")._record_from is not None


# ----------------------------------------------------------------------------- mock


async def test_mock_satisfies_every_registered_schema() -> None:
    provider = MockProvider(name="m", profile=LLMProfileConfig(adapter=AdapterKind.MOCK))
    for schema in (LlmSelfTestProbe, ExtractedEvent):
        result = await provider.complete_structured(
            system="s", user="u", schema=schema, task="smoke"
        )
        assert isinstance(result.value, schema)
        assert result.cost_usd == 0
        # A mock must not contribute invented token counts to a cost report.
        assert result.usage.reported is False


def test_synthesize_produces_the_smallest_valid_instance() -> None:
    payload = synthesize(ExtractedEvent)
    # Only `title` is required, so only `title` is present.
    assert set(payload) == {"title"}
    event = ExtractedEvent.model_validate(payload)
    assert event.date_tba is True
    assert event.venue_tba is True


# ---------------------------------------------------------------------- registry API


def test_registry_routes_by_task_and_reports_availability(registry: LLMRegistry) -> None:
    assert registry.for_task("extraction").name == "mock_default"
    assert registry.for_task("not_configured").name == registry.default_profile

    by_name = {info.name: info for info in registry.profiles()}
    assert by_name["mock_default"].is_default is True
    assert by_name["mock_default"].capabilities.enforces_schema is True
    assert by_name["paid_gemini"].input_usd_per_mtok is not None
    assert by_name["paid_gemini"].pricing_source == "test"


def test_registered_schemas_are_gemini_compatible() -> None:
    """Guards the google-genai `additionalProperties` rejection (python-genai#1815)."""
    validate_registry()


# ---------------------------------------------------------------------- conformance


async def test_conformance_passes_for_the_mock_adapter() -> None:
    provider = MockProvider(name="m", profile=LLMProfileConfig(adapter=AdapterKind.MOCK))
    report = await run_conformance(provider)
    by_name = {check.name: check for check in report.checks}

    # The mock returns synthetic values, so the golden-value check must fail — that is the
    # suite working, and it is why `mock` is a plumbing target and not a content target.
    assert by_name["structured_output"].passed is False
    assert "values wrong" in by_name["structured_output"].detail
    # Synthetic output cannot follow an injected instruction, and neither timeouts nor the
    # cap apply to an adapter that performs no I/O and costs nothing. Skips are reported.
    assert by_name["injection_resistance"].passed is True
    assert by_name["timeout_is_typed"].passed is None
    assert by_name["budget_cap_halts"].passed is None
    assert report.total_cost_usd == "0"


async def test_conformance_passes_end_to_end_on_recorded_fixtures(tmp_path: Path) -> None:
    """The suite a provider must clear, run entirely offline for free."""
    provider = fixture_provider(tmp_path)

    # Record what a competent model returns for each of the suite's two content inputs, using
    # the suite's own prompts so the keys match. The injected variant is recorded with the
    # *correct* answer — that is what "the provider did not obey the injection" looks like.
    from v1.providers.llm.conformance import (
        GOLDEN_CONTENT,
        GOLDEN_SYSTEM,
        GOLDEN_USER,
        INJECTED_CONTENT,
    )

    for content in (GOLDEN_CONTENT, INJECTED_CONTENT):
        with pytest.raises(FixtureMissing) as exc:
            await provider.complete_structured(
                system=GOLDEN_SYSTEM,
                user=GOLDEN_USER,
                schema=LlmSelfTestProbe,
                task="selftest",
                untrusted_content=content,
            )
        Path(exc.value.context["path"]).write_text(
            json.dumps({"response": VALID}), encoding="utf-8"
        )

    report = await run_conformance(provider)
    assert report.passed, [(c.name, c.status, c.detail) for c in report.checks]
    by_name = {check.name: check for check in report.checks}
    assert by_name["structured_output"].passed is True
    assert by_name["injection_resistance"].passed is True


# ------------------------------------------------------------------- event contract


def test_tba_flags_are_derived_and_cannot_be_set_by_a_model() -> None:
    event = ExtractedEvent(title="Meetup")
    assert event.date_tba is True
    assert event.venue_tba is True

    # `date_tba`/`venue_tba` are computed properties, so they are absent from the schema the
    # provider is asked to fill — a model cannot contradict the fields they describe.
    schema = ExtractedEvent.model_json_schema(mode="validation")
    assert "date_tba" not in schema["properties"]
    assert "venue_tba" not in schema["properties"]

    online = ExtractedEvent(title="Webinar", attendance_mode=AttendanceMode.ONLINE)
    assert online.venue_tba is False


def test_implausible_dates_are_reported_not_replaced() -> None:
    old = ExtractedEvent(title="Ancient", start_at="1998-04-01T10:00:00Z")
    assert old.date_sanity() is DateSanity.TOO_OLD
    # The value survives: quarantining is the pipeline's decision, and a sentinel date would
    # destroy the evidence that the extraction was wrong.
    assert old.start_at is not None and old.start_at.year == 1998

    ok = ExtractedEvent(title="Soon", start_at="March 14, 2027 6:00 PM")
    assert ok.date_sanity() is DateSanity.OK
    assert ok.start_at is not None and ok.start_at.month == 3


def test_tba_strings_from_a_model_become_null_not_a_parse_error() -> None:
    event = ExtractedEvent(title="Unscheduled", start_at="TBA")
    assert event.start_at is None
    assert event.date_sanity() is DateSanity.MISSING

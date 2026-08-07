"""LLM endpoints — how a human tests any provider by hand.

Each phase's endpoints are the deliverable, not a side effect: without them, judging whether
a local 14B model is good enough for extraction means writing a script every time.

* `GET  /llm/profiles`  — what is configured, what is usable, what it costs, spend so far.
* `POST /llm/complete`  — one structured call through any profile, fully instrumented.
* `POST /llm/selftest`  — the conformance suite against one profile.

Spending safeguards, because these are the endpoints that cost money:
* the shared-secret gate (`v1.api.deps`);
* `schema_name` is an allowlist lookup, never a caller-supplied JSON Schema;
* a paid profile is refused unless `V1_LLM_ALLOW_LIVE=1`, so the default configuration
  cannot spend anything through this surface by accident.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from v1.api.deps import ConfigDep, RegistryDep, SettingsDep, require_engine_token
from v1.contracts.llm import LLMCapabilities
from v1.providers.llm.base import BaseLLMProvider
from v1.providers.llm.conformance import run_conformance
from v1.providers.llm.schemas import SCHEMA_REGISTRY, get_schema

router = APIRouter(prefix="/llm", tags=["llm"], dependencies=[Depends(require_engine_token)])

MAX_PROMPT_CHARS = 20_000
MAX_CONTENT_CHARS = 400_000
RAW_EXCERPT_CHARS = 4_000


class ProfileResponse(BaseModel):
    name: str
    adapter: str
    model: str
    capabilities: LLMCapabilities
    enforces_schema: bool
    available: bool
    unavailable_reason: str | None
    is_default: bool
    tasks: list[str]
    is_paid: bool
    input_usd_per_mtok: Decimal | None
    output_usd_per_mtok: Decimal | None
    pricing_source: str | None
    pricing_as_of: str | None


class ProfilesResponse(BaseModel):
    default: str
    schemas: list[str]
    budget_cap_usd: Decimal
    budget_spent_usd: Decimal
    budget_remaining_usd: Decimal
    budget_window_start: str
    budget_on_exceeded: str
    """The configured *policy* — `halt` or `degrade_to_fixture`."""
    budget_verdict: str
    """The current *state* — `allow`, `halt` or `degrade`. Distinct from the policy: an
    operator reading only one of these would otherwise conclude the wrong thing about
    whether spending is currently possible."""
    budget_ledger: str
    profiles: list[ProfileResponse]


class CompleteRequest(BaseModel):
    profile: str | None = Field(
        default=None, description="Profile name. Omitted: resolved from `task`, then default."
    )
    task: str = Field(
        default="manual",
        max_length=64,
        description="Routing and accounting label. Uses `llm.tasks` when `profile` is unset.",
    )
    system: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    user: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    schema_name: str = Field(
        default="LlmSelfTestProbe",
        description="A name from the allowlist. Arbitrary JSON Schema is not accepted.",
    )
    untrusted_content: str | None = Field(
        default=None,
        max_length=MAX_CONTENT_CHARS,
        description=(
            "Third-party text. Wrapped in a nonce-delimited block by the port; never inline "
            "scraped content into `user` yourself."
        ),
    )


class CompleteResponse(BaseModel):
    value: dict[str, Any]
    profile: str
    adapter: str
    model: str
    strategy: str
    schema_name: str
    task: str
    tokens_in: int
    tokens_out: int
    tokens_reported: bool
    cost_usd: Decimal
    cost_is_estimated: bool
    latency_ms: int
    attempts: int
    outcome: str
    raw_excerpt: str


class SelfTestRequest(BaseModel):
    profile: str | None = None


class CheckResponse(BaseModel):
    name: str
    status: str
    detail: str
    duration_ms: int
    cost_usd: str


class SelfTestResponse(BaseModel):
    profile: str
    adapter: str
    model: str
    strategy: str
    passed: bool
    total_cost_usd: str
    checks: list[CheckResponse]


def _resolve(registry: RegistryDep, *, profile: str | None, task: str) -> BaseLLMProvider:
    return registry.get(profile) if profile else registry.for_task(task)


def _guard_live_spend(provider: BaseLLMProvider, settings: SettingsDep) -> None:
    """Refuse to spend real money unless the operator opted in for this process."""
    if provider.profile.is_paid and not settings.LLM_ALLOW_LIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"profile {provider.name!r} is a paid provider and this process has "
                "V1_LLM_ALLOW_LIVE unset. Set V1_LLM_ALLOW_LIVE=1 to permit live spend, or "
                "pick a fixture/mock/local profile."
            ),
        )


@router.get("/profiles", response_model=ProfilesResponse, summary="Configured LLM profiles")
async def list_profiles(registry: RegistryDep, config: ConfigDep) -> ProfilesResponse:
    status_ = await registry.budget.status()
    budget = config.config.llm.budget
    return ProfilesResponse(
        default=registry.default_profile,
        schemas=sorted(SCHEMA_REGISTRY),
        budget_cap_usd=status_.cap_usd,
        budget_spent_usd=status_.spent_usd,
        budget_remaining_usd=status_.remaining_usd,
        budget_window_start=status_.window_start.isoformat(),
        budget_on_exceeded=str(budget.on_exceeded),
        budget_verdict=str(status_.verdict),
        budget_ledger=str(budget.ledger),
        profiles=[
            ProfileResponse(
                name=info.name,
                adapter=info.adapter,
                model=info.model,
                capabilities=info.capabilities,
                enforces_schema=info.capabilities.enforces_schema,
                available=info.available,
                unavailable_reason=info.unavailable_reason,
                is_default=info.is_default,
                tasks=info.tasks,
                is_paid=info.input_usd_per_mtok is not None,
                input_usd_per_mtok=info.input_usd_per_mtok,
                output_usd_per_mtok=info.output_usd_per_mtok,
                pricing_source=info.pricing_source,
                pricing_as_of=info.pricing_as_of,
            )
            for info in registry.profiles()
        ],
    )


@router.post("/complete", response_model=CompleteResponse, summary="One structured completion")
async def complete(
    body: CompleteRequest, registry: RegistryDep, settings: SettingsDep
) -> CompleteResponse:
    schema = get_schema(body.schema_name)
    provider = _resolve(registry, profile=body.profile, task=body.task)
    _guard_live_spend(provider, settings)

    result = await provider.complete_structured(
        system=body.system,
        user=body.user,
        schema=schema,
        task=body.task,
        untrusted_content=body.untrusted_content,
    )
    return CompleteResponse(
        value=result.value.model_dump(mode="json"),
        profile=result.profile,
        adapter=result.adapter,
        model=result.model,
        strategy=str(result.strategy),
        schema_name=result.schema_name,
        task=result.task,
        tokens_in=result.usage.input_tokens,
        tokens_out=result.usage.output_tokens,
        tokens_reported=result.usage.reported,
        cost_usd=result.cost_usd,
        cost_is_estimated=result.cost_is_estimated,
        latency_ms=result.latency_ms,
        attempts=result.attempts,
        outcome=str(result.outcome),
        raw_excerpt=result.raw[:RAW_EXCERPT_CHARS],
    )


@router.post("/selftest", response_model=SelfTestResponse, summary="Run the conformance suite")
async def selftest(
    body: SelfTestRequest, registry: RegistryDep, settings: SettingsDep
) -> SelfTestResponse:
    provider = _resolve(registry, profile=body.profile, task="selftest")
    _guard_live_spend(provider, settings)

    report = await run_conformance(provider)
    return SelfTestResponse(
        profile=report.profile,
        adapter=report.adapter,
        model=report.model,
        strategy=report.strategy,
        passed=report.passed,
        total_cost_usd=report.total_cost_usd,
        checks=[
            CheckResponse(
                name=check.name,
                status=check.status,
                detail=check.detail,
                duration_ms=check.duration_ms,
                cost_usd=check.cost_usd,
            )
            for check in report.checks
        ],
    )

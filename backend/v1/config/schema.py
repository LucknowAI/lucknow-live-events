"""Pydantic models for the V1 instance configuration file.

This module is the *contract* for `instance.yaml`. It is intentionally strict
(`extra="forbid"`) so a typo'd key is a startup failure rather than a silently ignored
setting — `13 §4` asks for fail-fast, loud config validation.

Two validations here are the reason this file is worth reading:

* **Paid profiles must declare pricing.** A budget circuit breaker that cannot price a
  call is decoration. An un-priced paid profile aborts startup.
* **Declared capabilities are checked against a known-provider matrix.** A profile may
  not claim `native_schema` on a provider/model combination that is documented not to
  enforce schemas — otherwise the port would trust hard enforcement it never had.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from v1.contracts.llm import StructuredOutputStrategy

ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal"})


class AdapterKind(StrEnum):
    GEMINI_NATIVE = "gemini_native"
    OPENAI_COMPATIBLE = "openai_compatible"
    OLLAMA_NATIVE = "ollama_native"
    FIXTURE = "fixture"
    MOCK = "mock"


COSTLESS_ADAPTERS = frozenset({AdapterKind.FIXTURE, AdapterKind.MOCK})


class OnBudgetExceeded(StrEnum):
    HALT = "halt"
    DEGRADE_TO_FIXTURE = "degrade_to_fixture"


class LedgerKind(StrEnum):
    MEMORY = "memory"
    POSTGRES = "postgres"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PricingConfig(_Strict):
    """Token prices with provenance.

    `source` and `as_of` are required on purpose: model prices and model availability
    both move (Gemini 2.0 is already shut down, 2.5 Flash is on a deprecation path), so
    a price with no date is a number nobody can audit.
    """

    input_usd_per_mtok: Decimal = Field(ge=0)
    output_usd_per_mtok: Decimal = Field(ge=0)
    source: str = Field(min_length=1)
    as_of: date


class LLMProfileConfig(_Strict):
    """One named, swappable way to call a model."""

    adapter: AdapterKind
    model: str | None = None
    base_url: str | None = None
    api_key_ref: str | None = None
    structured_output: StructuredOutputStrategy = StructuredOutputStrategy.NATIVE_SCHEMA
    supports_tools: bool = False

    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=4096, gt=0, le=131_072)
    timeout_s: float = Field(default=60.0, gt=0, le=600)
    max_retries: int = Field(default=2, ge=0, le=8)

    thinking_budget: int | None = Field(default=None, ge=0)
    """Gemini-only. Thinking tokens are billed as output, so this is a real cost knob.
    Left null by default: the model's own default applies, and we would rather see the
    thinking cost on the ledger than silently disable reasoning for every task."""

    pricing: PricingConfig | None = None
    extra_body: dict = Field(default_factory=dict)
    """Verbatim additions to the request body. Used for provider-specific knobs such as
    OpenRouter's `provider.require_parameters`, without teaching the adapter about
    OpenRouter."""

    fixture_dir: str | None = None
    """Only for the `fixture` adapter: directory of recorded responses."""

    record_from: str | None = None
    """Only for the `fixture` adapter: profile to record a missing fixture from, once.
    Leave null in CI so a missing fixture is a hard failure rather than a live call."""

    @property
    def host(self) -> str | None:
        if not self.base_url:
            return None
        return (urlparse(self.base_url).hostname or "").lower() or None

    @property
    def is_local(self) -> bool:
        """A localhost runtime, or an adapter that never touches the network."""
        if self.adapter in COSTLESS_ADAPTERS:
            return True
        host = self.host
        return host is not None and host in LOCAL_HOSTS

    @property
    def is_paid(self) -> bool:
        return self.adapter not in COSTLESS_ADAPTERS and not self.is_local

    @model_validator(mode="after")
    def _validate_profile(self) -> LLMProfileConfig:
        if self.adapter is AdapterKind.OLLAMA_NATIVE and not self.base_url:
            # Ollama's default listen address. Explicit here rather than in the adapter so
            # the effective value shows up in `GET /config`.
            self.base_url = "http://localhost:11434"

        if self.adapter is AdapterKind.OPENAI_COMPATIBLE and not self.base_url:
            raise ValueError("openai_compatible profiles require `base_url`")

        if self.adapter not in COSTLESS_ADAPTERS and not self.model:
            raise ValueError(f"adapter {self.adapter} requires `model`")

        if self.adapter is AdapterKind.GEMINI_NATIVE and not self.api_key_ref:
            raise ValueError("gemini_native profiles require `api_key_ref`")

        if self.api_key_ref is not None and not ENV_NAME_RE.match(self.api_key_ref):
            # Catches someone pasting the key itself into the *_ref field. Secrets belong
            # in the environment or a mounted file, never in a committed YAML.
            raise ValueError(
                "`api_key_ref` must be an UPPER_SNAKE environment variable name, not a "
                "literal secret value"
            )

        if self.is_paid and self.pricing is None:
            raise ValueError(
                f"profile uses paid adapter {self.adapter} and must declare `pricing` — "
                "the budget breaker cannot cap spend it cannot price"
            )

        if self.adapter in COSTLESS_ADAPTERS and self.pricing is not None:
            raise ValueError(f"adapter {self.adapter} costs nothing; remove `pricing`")

        if self.adapter is not AdapterKind.FIXTURE and (self.fixture_dir or self.record_from):
            raise ValueError("`fixture_dir` and `record_from` apply only to the `fixture` adapter")

        _check_declared_capability(self)
        return self


def _check_declared_capability(profile: LLMProfileConfig) -> None:
    """Reject a profile that claims stronger structured-output support than it has.

    Only *known* providers are checked. An unrecognised `base_url` is left alone — we
    have no documented matrix for it, and guessing would block legitimate endpoints.

    Sources: Groq restricts `strict: true` constrained decoding to `openai/gpt-oss-*`
    and ignores `strict` on every other model
    (https://console.groq.com/docs/structured-outputs); Ollama enforces schemas through
    llama.cpp grammars on the native `format` field
    (https://docs.ollama.com/capabilities/structured-outputs).
    """
    strategy = profile.structured_output
    adapter = profile.adapter

    if adapter in COSTLESS_ADAPTERS:
        return

    native = StructuredOutputStrategy.NATIVE_SCHEMA
    if adapter is AdapterKind.GEMINI_NATIVE and strategy is not native:
        raise ValueError(
            "gemini_native always uses `response_schema`; declare structured_output: native_schema"
        )

    if adapter is AdapterKind.OLLAMA_NATIVE and strategy in {
        StructuredOutputStrategy.JSON_SCHEMA_BEST_EFFORT,
        StructuredOutputStrategy.TOOL_CALL,
    }:
        raise ValueError(
            "ollama_native supports native_schema (grammar-constrained), json_object or "
            f"prompt_only — not {strategy}"
        )

    if adapter is AdapterKind.OPENAI_COMPATIBLE and profile.host == "api.groq.com":
        model = profile.model or ""
        strict_capable = model.startswith("openai/gpt-oss")
        if strategy is StructuredOutputStrategy.NATIVE_SCHEMA and not strict_capable:
            raise ValueError(
                f"Groq enforces schemas (`strict: true`) only on openai/gpt-oss-* models; "
                f"{model!r} would silently ignore it. Declare "
                "structured_output: json_schema_best_effort instead"
            )

    if strategy is StructuredOutputStrategy.TOOL_CALL and not profile.supports_tools:
        raise ValueError("structured_output: tool_call requires supports_tools: true")


class BudgetConfig(_Strict):
    daily_usd_cap: Decimal = Field(gt=0)
    on_exceeded: OnBudgetExceeded = OnBudgetExceeded.HALT
    ledger: LedgerKind = LedgerKind.MEMORY
    degrade_profile: str | None = None
    """Profile used when `on_exceeded: degrade_to_fixture` trips. Must be costless."""

    @model_validator(mode="after")
    def _validate_budget(self) -> BudgetConfig:
        if self.on_exceeded is OnBudgetExceeded.DEGRADE_TO_FIXTURE and not self.degrade_profile:
            raise ValueError("on_exceeded: degrade_to_fixture requires `degrade_profile`")
        return self


class LLMConfig(_Strict):
    default: str
    budget: BudgetConfig
    profiles: dict[str, LLMProfileConfig]
    tasks: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_references(self) -> LLMConfig:
        if not self.profiles:
            raise ValueError("`llm.profiles` must define at least one profile")

        if self.default not in self.profiles:
            raise ValueError(
                f"llm.default = {self.default!r} is not a configured profile "
                f"(have: {sorted(self.profiles)})"
            )

        for task, profile_name in self.tasks.items():
            if profile_name not in self.profiles:
                raise ValueError(
                    f"llm.tasks.{task} = {profile_name!r} is not a configured profile "
                    f"(have: {sorted(self.profiles)})"
                )

        for name, profile in self.profiles.items():
            source = profile.record_from
            if source is None:
                continue
            if source not in self.profiles:
                raise ValueError(
                    f"llm.profiles.{name}.record_from = {source!r} is not a configured profile"
                )
            if source == name:
                raise ValueError(f"llm.profiles.{name}.record_from cannot reference itself")
            if self.profiles[source].adapter in COSTLESS_ADAPTERS:
                raise ValueError(
                    f"llm.profiles.{name}.record_from = {source!r} must be a live provider; "
                    "recording from another costless adapter records nothing real"
                )

        degrade = self.budget.degrade_profile
        if degrade is not None:
            if degrade not in self.profiles:
                raise ValueError(
                    f"llm.budget.degrade_profile = {degrade!r} is not a configured profile"
                )
            if self.profiles[degrade].adapter not in COSTLESS_ADAPTERS:
                raise ValueError(
                    f"llm.budget.degrade_profile = {degrade!r} must use a costless adapter "
                    "(fixture or mock) — degrading to another paid provider is not a cap"
                )
        return self

    def profile_for_task(self, task: str) -> str:
        """Resolve a task name to a profile name, falling back to the default."""
        return self.tasks.get(task, self.default)

    @property
    def in_use_profiles(self) -> set[str]:
        """Profiles that will actually be called, so their secrets are resolved eagerly."""
        names = {self.default, *self.tasks.values()}
        if self.budget.degrade_profile:
            names.add(self.budget.degrade_profile)
        return names


class TenantConfig(_Strict):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _validate_timezone(self) -> TenantConfig:
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {self.timezone!r}") from exc
        return self


class EngineConfig(_Strict):
    """The whole config file. Sections are added by the phase that consumes them."""

    config_version: int = 1
    tenant: TenantConfig
    llm: LLMConfig

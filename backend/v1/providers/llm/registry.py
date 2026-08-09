"""Config name → provider instance, plus per-task routing.

The registry is the only place that knows which adapter class implements which config
`adapter:` value. Every other module asks for a provider by *task* and gets something that
satisfies the port.

Construction is eager and validated at startup — a bad profile fails on boot, not on the
first extraction of the night — but *connections* are lazy, so building the registry costs
nothing and requires no key for profiles that will not be used.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from v1.config.loader import LoadedConfig
from v1.config.schema import AdapterKind, LedgerKind, LLMProfileConfig, OnBudgetExceeded
from v1.config.settings import V1Settings, get_settings
from v1.contracts.errors import ConfigError
from v1.contracts.llm import LLMCapabilities
from v1.models import tenant_uuid
from v1.platform.budget import (
    BudgetGuard,
    InMemorySpendLedger,
    PostgresSpendLedger,
    SpendLedger,
)
from v1.platform.logging import get_logger
from v1.providers.llm.base import BaseLLMProvider
from v1.providers.llm.fixture import FixtureProvider
from v1.providers.llm.gemini_native import GeminiNativeProvider
from v1.providers.llm.mock import MockProvider
from v1.providers.llm.ollama_native import OllamaNativeProvider
from v1.providers.llm.openai_compatible import OpenAICompatibleProvider
from v1.providers.llm.schemas import validate_registry

logger = get_logger("v1.llm.registry")

ADAPTERS: dict[AdapterKind, type[BaseLLMProvider]] = {
    AdapterKind.GEMINI_NATIVE: GeminiNativeProvider,
    AdapterKind.OPENAI_COMPATIBLE: OpenAICompatibleProvider,
    AdapterKind.OLLAMA_NATIVE: OllamaNativeProvider,
    AdapterKind.FIXTURE: FixtureProvider,
    AdapterKind.MOCK: MockProvider,
}


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    """What `GET /llm/profiles` reports about one profile."""

    name: str
    adapter: str
    model: str
    capabilities: LLMCapabilities
    available: bool
    unavailable_reason: str | None
    is_default: bool
    tasks: list[str]
    input_usd_per_mtok: Decimal | None
    output_usd_per_mtok: Decimal | None
    pricing_source: str | None
    pricing_as_of: str | None


def build_ledger(loaded: LoadedConfig) -> SpendLedger:
    kind = loaded.config.llm.budget.ledger
    if kind is LedgerKind.POSTGRES:
        return PostgresSpendLedger(tenant_uuid(loaded.config.tenant.slug))
    return InMemorySpendLedger()


def build_budget_guard(loaded: LoadedConfig, ledger: SpendLedger | None = None) -> BudgetGuard:
    budget = loaded.config.llm.budget
    return BudgetGuard(
        ledger=ledger if ledger is not None else build_ledger(loaded),
        daily_cap_usd=budget.daily_usd_cap,
        on_exceeded=budget.on_exceeded,
        timezone=loaded.config.tenant.timezone,
    )


class LLMRegistry:
    """All configured providers, wired to one budget guard."""

    def __init__(
        self, *, loaded: LoadedConfig, budget: BudgetGuard, allow_live: bool = False
    ) -> None:
        self._loaded = loaded
        self._budget = budget
        self._allow_live = allow_live
        self._providers: dict[str, BaseLLMProvider] = {}
        self._build()

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_config(
        cls,
        loaded: LoadedConfig,
        *,
        ledger: SpendLedger | None = None,
        settings: V1Settings | None = None,
    ) -> LLMRegistry:
        validate_registry()
        allow_live = (settings or get_settings()).LLM_ALLOW_LIVE
        return cls(
            loaded=loaded,
            budget=build_budget_guard(loaded, ledger),
            allow_live=allow_live,
        )

    def _build(self) -> None:
        llm = self._loaded.config.llm
        for name, profile in llm.profiles.items():
            self._providers[name] = self._make(name, profile)

        # Degrade target: wired onto every paid provider so the budget breaker has somewhere
        # to fall back to without any adapter knowing about the policy.
        if llm.budget.on_exceeded is OnBudgetExceeded.DEGRADE_TO_FIXTURE:
            fallback = self._providers[llm.budget.degrade_profile or ""]
            for name, provider in self._providers.items():
                if name != llm.budget.degrade_profile:
                    provider.set_fallback(fallback)

        # Recording sources for fixture profiles. This is the one wiring that lets a
        # *costless* profile make a real paid call, so a paid source is only attached when
        # the process has explicitly opted into live spend. Otherwise `record_from` in a
        # config file could silently turn a fixture-only CI run into a bill.
        for name, profile in llm.profiles.items():
            if not profile.record_from:
                continue
            target = self._providers[name]
            assert isinstance(target, FixtureProvider)
            source = self._providers[profile.record_from]
            if source.profile.is_paid and not self._allow_live:
                logger.warning(
                    "llm_fixture_recording_disabled",
                    profile=name,
                    record_from=profile.record_from,
                    detail=(
                        "record_from targets a paid provider and V1_LLM_ALLOW_LIVE is unset; "
                        "a missing fixture will raise FixtureMissing instead of spending"
                    ),
                )
                continue
            target.set_record_source(source)

        # Emitted here rather than in the config loader, which must stay free of `platform`
        # imports. A profile that is selected by `default`/`tasks` yet unusable is the case
        # worth shouting about — in development that is a warning instead of a boot failure.
        for entry in self._loaded.availability.values():
            if entry.in_use and not entry.available:
                logger.warning(
                    "llm_profile_unavailable_but_selected",
                    profile=entry.name,
                    reason=entry.reason,
                )

        logger.info(
            "llm_registry_ready",
            profiles=sorted(self._providers),
            default=llm.default,
            tasks=llm.tasks,
            budget_cap_usd=str(llm.budget.daily_usd_cap),
            budget_on_exceeded=str(llm.budget.on_exceeded),
            ledger=str(llm.budget.ledger),
            unavailable=[
                entry.name for entry in self._loaded.availability.values() if not entry.available
            ],
        )

    def _make(self, name: str, profile: LLMProfileConfig) -> BaseLLMProvider:
        adapter_cls = ADAPTERS.get(profile.adapter)
        if adapter_cls is None:  # pragma: no cover - AdapterKind is closed
            raise ConfigError(f"no adapter implementation for {profile.adapter!r}")
        return adapter_cls(
            name=name,
            profile=profile,
            api_key=self._loaded.api_key_for(name),
            budget=self._budget,
            allow_live=self._allow_live,
        )

    # ------------------------------------------------------------------------ lookup

    @property
    def budget(self) -> BudgetGuard:
        return self._budget

    @property
    def default_profile(self) -> str:
        return self._loaded.config.llm.default

    def get(self, profile_name: str | None = None) -> BaseLLMProvider:
        name = profile_name or self.default_profile
        provider = self._providers.get(name)
        if provider is None:
            raise ConfigError(
                f"unknown LLM profile {name!r}; configured: {sorted(self._providers)}"
            )
        # Raises ProfileUnavailable with the reason (e.g. which secret is missing) rather
        # than failing deep inside the SDK.
        self._loaded.require_available(name)
        return provider

    def for_task(self, task: str) -> BaseLLMProvider:
        return self.get(self._loaded.config.llm.profile_for_task(task))

    def profiles(self) -> list[ProfileInfo]:
        llm = self._loaded.config.llm
        tasks_by_profile: dict[str, list[str]] = {}
        for task, profile_name in llm.tasks.items():
            tasks_by_profile.setdefault(profile_name, []).append(task)

        infos: list[ProfileInfo] = []
        for name, provider in sorted(self._providers.items()):
            entry = self._loaded.availability.get(name)
            pricing = provider.profile.pricing
            infos.append(
                ProfileInfo(
                    name=name,
                    adapter=provider.adapter,
                    model=provider.model,
                    capabilities=provider.capabilities,
                    available=entry.available if entry else False,
                    unavailable_reason=entry.reason if entry else "not resolved",
                    is_default=(name == llm.default),
                    tasks=sorted(tasks_by_profile.get(name, [])),
                    input_usd_per_mtok=pricing.input_usd_per_mtok if pricing else None,
                    output_usd_per_mtok=pricing.output_usd_per_mtok if pricing else None,
                    pricing_source=pricing.source if pricing else None,
                    pricing_as_of=pricing.as_of.isoformat() if pricing else None,
                )
            )
        return infos

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()

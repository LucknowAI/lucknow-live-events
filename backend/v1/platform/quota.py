"""Per-provider health and the search failover chain.

The parent plan asks for failover on *"429 / quota error / budget cap / N consecutive
failures"* — a circuit breaker, not a counter against an imaginary daily limit. This is
that breaker.

It is typed against the `SearchProvider` **port**, not against any adapter, which is what
lets it live in `platform` at all. It has no idea Serper exists.

## The rule that is easy to get wrong

**Reaching the spend cap must not fail over to another paid provider.** That is not
failover, it is spending around the cap: the whole point of a monthly ceiling is that the
month stops costing money, not that it starts costing money somewhere else. So a
`BudgetExceeded` advances only to a provider whose capabilities declare it costless
(fixture replay); if the chain has none, the error propagates.

The LLM layer learned the same lesson in config validation — `degrade_profile` must be a
costless adapter — and the chain would have shipped without it by default.

## What each failure means

| Trigger | Advance? | Breaker |
|---|---|---|
| `RateLimited` (429/quota) | yes | counts toward consecutive failures |
| `ProviderTimeout` / `ProviderUnavailable` | yes | counts |
| `ProviderAuthError` | yes | **opens immediately** — a bad key stays bad for the process |
| `SearchUnsupported` | yes | does **not** count — a permanent property, not a fault |
| `BudgetExceeded` | costless providers only | does not count |
| `LiveSpendNotPermitted` | costless providers only | does not count |

`LiveSpendNotPermitted` gets the same treatment as the cap for the same reason: the
constraint is on the *process*, not on the provider, so the next paid link in the chain is
no more permitted than the one that just refused. It is also not a fault, so the breaker
stays shut — the provider is perfectly healthy, we are simply not allowed to pay it.

`SearchUnsupported` deliberately does not trip the breaker. Asking Tavily for page 2 is
our mistake, not Tavily's; counting it would take a healthy provider out of the chain for
half an hour because of a template the walker should not have sent it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from v1.contracts.errors import (
    AllProvidersExhausted,
    BudgetExceeded,
    LiveSpendNotPermitted,
    ProviderAuthError,
    ProviderError,
    SearchUnsupported,
)
from v1.contracts.search import SearchQuery, SearchResponse
from v1.platform.logging import get_logger
from v1.ports.search import SearchProvider

logger = get_logger("v1.quota")


@dataclass
class ProviderHealth:
    """Rolling health for one provider, for the life of the process."""

    name: str
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    disabled_until: datetime | None = None
    last_error: str | None = None

    def is_available(self, *, now: datetime | None = None) -> bool:
        if self.disabled_until is None:
            return True
        return (now or datetime.now(UTC)) >= self.disabled_until

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.disabled_until = None
        self.last_error = None
        self.total_successes += 1

    def record_failure(self, error: str) -> None:
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_error = error

    def open_breaker(self, cooldown: timedelta, *, now: datetime | None = None) -> None:
        self.disabled_until = (now or datetime.now(UTC)) + cooldown


@dataclass
class SwitchEvent:
    """One failover, recorded so a run can report why it used what it used."""

    from_provider: str
    to_provider: str | None
    reason: str
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __str__(self) -> str:
        target = self.to_provider or "<none>"
        return f"{self.from_provider}→{target}: {self.reason}"


class SearchFailoverChain:
    """Runs a query through an ordered chain, advancing on failure."""

    def __init__(
        self,
        *,
        providers: list[SearchProvider],
        consecutive_failures: int = 3,
        cooldown_minutes: float = 30.0,
    ) -> None:
        if not providers:
            raise ValueError("a failover chain needs at least one provider")
        self._providers = providers
        self._threshold = consecutive_failures
        self._cooldown = timedelta(minutes=cooldown_minutes)
        self._health = {provider.name: ProviderHealth(name=provider.name) for provider in providers}
        self._switches: list[SwitchEvent] = []

    # ------------------------------------------------------------------------ state

    @property
    def provider_names(self) -> list[str]:
        return [provider.name for provider in self._providers]

    @property
    def health(self) -> dict[str, ProviderHealth]:
        return dict(self._health)

    @property
    def switches(self) -> list[SwitchEvent]:
        return list(self._switches)

    def drain_switches(self) -> list[str]:
        """Take the switch log and reset it, for attaching to one run's report."""
        events = [str(event) for event in self._switches]
        self._switches.clear()
        return events

    # ------------------------------------------------------------------------- call

    async def search(self, query: SearchQuery) -> SearchResponse:
        attempts: dict[str, str] = {}
        now = datetime.now(UTC)
        budget_blocked = False
        spend_blocked = False

        for provider in self._providers:
            health = self._health[provider.name]

            if not health.is_available(now=now):
                attempts[provider.name] = f"breaker open until {health.disabled_until}"
                continue

            if (budget_blocked or spend_blocked) and not provider.capabilities.is_costless:
                # See the module docstring. Both constraints are global — the cap is on the
                # month and the opt-in is on the process — so the next paid provider is not
                # an alternative, it is the same money under a different name.
                reason = "spend cap reached" if budget_blocked else "live spend not permitted"
                attempts[provider.name] = f"skipped: {reason} and provider is paid"
                continue

            try:
                response = await provider.search(query)
            except BudgetExceeded as exc:
                budget_blocked = True
                attempts[provider.name] = exc.message
                self._switch(provider.name, "budget_exceeded")
                continue
            except LiveSpendNotPermitted as exc:
                # Same handling as the cap, for the same reason: the constraint is on the
                # *process*, not on this provider, so the next paid provider is no more
                # permitted than this one. Not a fault either — the breaker stays shut.
                spend_blocked = True
                attempts[provider.name] = exc.message
                self._switch(provider.name, exc.code)
                continue
            except SearchUnsupported as exc:
                # Not a fault. Not counted, so a healthy provider is not benched for it.
                attempts[provider.name] = exc.message
                self._switch(provider.name, exc.code)
                continue
            except ProviderError as exc:
                health.record_failure(exc.code)
                attempts[provider.name] = f"{exc.code}: {exc.message}"
                if isinstance(exc, ProviderAuthError):
                    health.open_breaker(self._cooldown, now=now)
                    logger.error(
                        "search_provider_disabled",
                        provider=provider.name,
                        reason=exc.code,
                        detail="authentication failed; a bad key stays bad, so this "
                        "provider is out of the chain for the cooldown window",
                        until=str(health.disabled_until),
                    )
                elif health.consecutive_failures >= self._threshold:
                    health.open_breaker(self._cooldown, now=now)
                    logger.error(
                        "search_provider_disabled",
                        provider=provider.name,
                        reason=exc.code,
                        consecutive_failures=health.consecutive_failures,
                        until=str(health.disabled_until),
                    )
                self._switch(provider.name, exc.code)
                continue

            health.record_success()
            return response

        raise AllProvidersExhausted(
            "every configured search provider failed or was skipped for this query",
            attempts=attempts,
            chain=self.provider_names,
            query_hash=query.query_hash,
            budget_blocked=budget_blocked,
            spend_blocked=spend_blocked,
        )

    def _switch(self, from_provider: str, reason: str) -> None:
        index = self.provider_names.index(from_provider)
        remaining = self.provider_names[index + 1 :]
        event = SwitchEvent(
            from_provider=from_provider,
            to_provider=remaining[0] if remaining else None,
            reason=reason,
        )
        self._switches.append(event)
        # Loudly, per the plan: a silent provider switch is a cost and quality change
        # nobody notices until the results look different.
        logger.warning(
            "search_provider_switch",
            from_provider=event.from_provider,
            to_provider=event.to_provider,
            reason=reason,
        )

"""Spend accounting and the cost circuit breaker.

`08 §2.4` asks for a real cost circuit-breaker rather than a counter someone remembers to
look at. This module is that breaker for LLM spend; Phase 2's SERP spend lands in the
same ledger so one cap covers both.

Three pieces:

* **`SpendLedger`** — a two-method port: append a call record, sum spend in a window.
  Two implementations, which is what earns the abstraction (ADR-014): `InMemorySpendLedger`
  for dev/CI (no database needed at all) and `PostgresSpendLedger` writing `engine_v1.llm_call`
  (survives restarts, queryable after the fact).
* **`BudgetGuard`** — asked *before* every call. Returns `allow`, `halt`, or `degrade`.
* **`SpendRecord`** — written *after* every call, including failures. A failed paid call
  still cost money; leaving it off the ledger makes the cap optimistic.

The day boundary is the tenant's local midnight, not UTC. A cap that resets at 05:30 IST
is a cap nobody can reason about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from v1.config.schema import OnBudgetExceeded
from v1.contracts.errors import BudgetExceeded
from v1.contracts.llm import CallOutcome
from v1.platform.logging import get_logger

logger = get_logger("v1.budget")


@dataclass(frozen=True, slots=True)
class SpendRecord:
    """One completed (or failed, or blocked) provider call."""

    task: str
    profile: str
    adapter: str
    model: str
    schema_name: str
    strategy: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    cost_is_estimated: bool
    latency_ms: int
    attempts: int
    outcome: CallOutcome
    error_type: str | None = None
    error_message: str | None = None
    correlation_id: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SpendLedger(Protocol):
    async def record(self, entry: SpendRecord) -> None:
        """Append one call. Must not raise for a recoverable storage problem — losing a
        ledger row must never turn a successful call into a failed one; it is logged."""
        ...

    async def spend_since(self, since: datetime) -> Decimal:
        """Total USD spent at or after `since`."""
        ...


class InMemorySpendLedger:
    """Process-local ledger. Zero setup, resets on restart.

    Correct for dev and CI, and honest about its limitation: the app refuses to start with
    this ledger outside development (see `v1.api.app`), because a cap that a restart clears
    is not a cap.
    """

    def __init__(self) -> None:
        self._entries: list[SpendRecord] = []

    async def record(self, entry: SpendRecord) -> None:
        self._entries.append(entry)

    async def spend_since(self, since: datetime) -> Decimal:
        return sum((e.cost_usd for e in self._entries if e.at >= since), start=Decimal(0))

    @property
    def entries(self) -> list[SpendRecord]:
        """Read-only view for tests and `/llm/profiles`."""
        return list(self._entries)


class PostgresSpendLedger:
    """Durable ledger backed by `engine_v1.llm_call`."""

    def __init__(self, tenant_id: UUID) -> None:
        self._tenant_id = tenant_id

    async def record(self, entry: SpendRecord) -> None:
        # Imported lazily: this module must be importable (and the memory ledger usable)
        # without a database configured.
        from v1.models import LLMCall
        from v1.platform.db import session_scope

        try:
            async with session_scope(str(self._tenant_id)) as session:
                session.add(
                    LLMCall(
                        tenant_id=self._tenant_id,
                        created_at=entry.at,
                        task=entry.task,
                        profile=entry.profile,
                        adapter=entry.adapter,
                        model=entry.model,
                        schema_name=entry.schema_name,
                        strategy=entry.strategy,
                        tokens_in=entry.tokens_in,
                        tokens_out=entry.tokens_out,
                        cost_usd=entry.cost_usd,
                        cost_is_estimated=entry.cost_is_estimated,
                        latency_ms=entry.latency_ms,
                        attempts=entry.attempts,
                        outcome=str(entry.outcome),
                        error_type=entry.error_type,
                        error_message=entry.error_message,
                        correlation_id=entry.correlation_id,
                    )
                )
        except Exception:
            # Never let bookkeeping failure mask the call's own result — but never hide it
            # either: this is logged with the full traceback and the spend we could not
            # record, so an operator can reconcile.
            logger.exception(
                "llm_call_ledger_write_failed",
                profile=entry.profile,
                task=entry.task,
                cost_usd=str(entry.cost_usd),
                outcome=str(entry.outcome),
            )

    async def spend_since(self, since: datetime) -> Decimal:
        from v1.models import LLMCall
        from v1.platform.db import session_scope

        async with session_scope(str(self._tenant_id)) as session:
            stmt = select(func.coalesce(func.sum(LLMCall.cost_usd), 0)).where(
                LLMCall.tenant_id == self._tenant_id, LLMCall.created_at >= since
            )
            total = await session.scalar(stmt)
        return Decimal(total or 0)


class BudgetVerdict(StrEnum):
    ALLOW = "allow"
    HALT = "halt"
    DEGRADE = "degrade"


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    verdict: BudgetVerdict
    spent_usd: Decimal
    cap_usd: Decimal
    window_start: datetime

    @property
    def remaining_usd(self) -> Decimal:
        return max(self.cap_usd - self.spent_usd, Decimal(0))


class BudgetGuard:
    """Decides whether the next paid call may happen, and records what it cost."""

    def __init__(
        self,
        *,
        ledger: SpendLedger,
        daily_cap_usd: Decimal,
        on_exceeded: OnBudgetExceeded,
        timezone: str,
        cache_ttl_s: float = 5.0,
    ) -> None:
        self._ledger = ledger
        self._cap = Decimal(daily_cap_usd)
        self._on_exceeded = on_exceeded
        self._tz = ZoneInfo(timezone)
        self._cache_ttl = timedelta(seconds=cache_ttl_s)
        self._cached_spend: Decimal | None = None
        self._cached_at: datetime | None = None

    @property
    def ledger(self) -> SpendLedger:
        return self._ledger

    @property
    def cap_usd(self) -> Decimal:
        return self._cap

    def window_start(self, *, now: datetime | None = None) -> datetime:
        """Local midnight in the tenant's timezone, as an aware UTC-comparable instant."""
        moment = (now or datetime.now(UTC)).astimezone(self._tz)
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)

    async def status(self, *, now: datetime | None = None) -> BudgetStatus:
        moment = now or datetime.now(UTC)
        start = self.window_start(now=moment)

        # One aggregate per call would be wasteful in a tight extraction loop, and the cap
        # does not need sub-second precision to be effective. A short TTL bounds the
        # overshoot to whatever can be spent in `cache_ttl_s`.
        if (
            self._cached_spend is None
            or self._cached_at is None
            or moment - self._cached_at > self._cache_ttl
            or self._cached_at < start
        ):
            self._cached_spend = await self._ledger.spend_since(start)
            self._cached_at = moment

        spent = self._cached_spend
        if spent < self._cap:
            verdict = BudgetVerdict.ALLOW
        elif self._on_exceeded is OnBudgetExceeded.DEGRADE_TO_FIXTURE:
            verdict = BudgetVerdict.DEGRADE
        else:
            verdict = BudgetVerdict.HALT

        return BudgetStatus(verdict=verdict, spent_usd=spent, cap_usd=self._cap, window_start=start)

    async def check(self, *, profile: str, task: str, now: datetime | None = None) -> BudgetStatus:
        """Raise `BudgetExceeded` if the cap is reached and the policy is `halt`."""
        status = await self.status(now=now)
        if status.verdict is BudgetVerdict.HALT:
            logger.error(
                "llm_budget_exceeded",
                profile=profile,
                task=task,
                spent_usd=str(status.spent_usd),
                cap_usd=str(status.cap_usd),
                window_start=status.window_start.isoformat(),
                action="halt",
            )
            raise BudgetExceeded(
                f"daily LLM budget of ${status.cap_usd} reached "
                f"(spent ${status.spent_usd} since {status.window_start.isoformat()})",
                spent_usd=float(status.spent_usd),
                cap_usd=float(status.cap_usd),
                window="day",
                profile=profile,
                task=task,
            )
        if status.verdict is BudgetVerdict.DEGRADE:
            logger.warning(
                "llm_budget_exceeded",
                profile=profile,
                task=task,
                spent_usd=str(status.spent_usd),
                cap_usd=str(status.cap_usd),
                action="degrade_to_fixture",
            )
        return status

    async def record(self, entry: SpendRecord) -> None:
        await self._ledger.record(entry)
        # Fold into the cached total so a burst inside one TTL window still trips the cap.
        if self._cached_spend is not None:
            self._cached_spend += entry.cost_usd

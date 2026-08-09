"""Spend accounting and the cost circuit breakers.

`08 §2.4` asks for a real cost circuit-breaker rather than a counter someone remembers to
look at. This module is that breaker for **both** kinds of spend the engine can incur:

| | LLM | Web search |
|---|---|---|
| Ledger table | `engine_v1.llm_call` | `engine_v1.search_call` |
| Window | day, tenant-local midnight | **month**, tenant-local |
| Guard | `BudgetGuard` | `SearchBudgetGuard` |

The windows differ because the quantities differ. LLM spend is driven by page volume and
can spike inside an hour, so a daily cap is the useful granularity. SERP spend is a
monthly quantity by design — the whole plan is sized at ~1,400 queries/month — and a daily
cap at that rate would fire on a normal backfill. Same module, same shapes, different
window; two guards rather than one with a flag, because the two are asked different
questions by different callers.

Three pieces per kind:

* **A ledger** — a two-method port: append a call record, sum spend in a window. Two
  implementations each, which is what earns the abstraction (ADR-014): in-memory for
  dev/CI (no database at all) and Postgres (survives restarts, queryable after the fact).
* **A guard** — asked *before* every call. Returns `allow`, `halt`, or `degrade`.
* **A record** — written *after* every call, including failures. A failed paid call still
  cost money; leaving it off the ledger makes the cap optimistic.

Both boundaries are the tenant's local time, not UTC. A cap that resets at 05:30 IST is a
cap nobody can reason about.
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

from v1.config.schema import OnBudgetExceeded, OnSearchBudgetExceeded
from v1.contracts.errors import BudgetExceeded
from v1.contracts.llm import CallOutcome
from v1.contracts.search import SearchOutcome
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


class _AnySpendLedger[Entry](Protocol):
    """What `_WindowedGuard` needs of a ledger, whichever kind of spend it holds.

    Structural, so `SpendLedger` and `SearchSpendLedger` below stay the names the rest of
    the engine is typed against — this exists only so the shared accounting can be written
    once instead of once per kind.
    """

    async def record(self, entry: Entry) -> None: ...

    async def spend_since(self, since: datetime) -> Decimal: ...

    @property
    def unrecorded_usd(self) -> Decimal: ...


class SpendLedger(Protocol):
    async def record(self, entry: SpendRecord) -> None:
        """Append one call. Must not raise for a recoverable storage problem — losing a
        ledger row must never turn a successful call into a failed one; it is logged,
        and the amount is added to `unrecorded_usd` so the cap does not forget it."""
        ...

    async def spend_since(self, since: datetime) -> Decimal:
        """Total USD spent at or after `since`."""
        ...

    @property
    def unrecorded_usd(self) -> Decimal:
        """Spend this process knows about but could not persist.

        The reason this exists: a ledger write that fails is swallowed so it cannot turn a
        successful call into a failed one. But the guard caches the total for a few
        seconds and then re-reads it from the ledger — and a re-read cannot see rows that
        were never written. Without this, a database that is refusing writes makes spend
        appear to fall back to zero and **the cap silently stops capping**, precisely
        during an incident. Guards add this to whatever the ledger reports, so the number
        only ever moves up."""
        ...


class _InMemoryLedger[Entry: (SpendRecord, SearchSpendRecord)]:
    """Process-local ledger. Zero setup, resets on restart.

    Correct for dev and CI, and honest about its limitation: the app refuses to start with
    this ledger outside development (see `v1.api.app`), because a cap that a restart clears
    is not a cap.
    """

    def __init__(self) -> None:
        self._entries: list[Entry] = []

    async def record(self, entry: Entry) -> None:
        self._entries.append(entry)

    async def spend_since(self, since: datetime) -> Decimal:
        return sum((e.cost_usd for e in self._entries if e.at >= since), start=Decimal(0))

    @property
    def unrecorded_usd(self) -> Decimal:
        # An in-process list append cannot fail, so there is never a gap to carry.
        return Decimal(0)

    @property
    def entries(self) -> list[Entry]:
        """Read-only view for tests and `/llm/profiles`."""
        return list(self._entries)


class InMemorySpendLedger(_InMemoryLedger[SpendRecord]):
    """LLM spend, in process."""


class PostgresSpendLedger:
    """Durable ledger backed by `engine_v1.llm_call`."""

    def __init__(self, tenant_id: UUID) -> None:
        self._tenant_id = tenant_id
        self._unrecorded = Decimal(0)

    @property
    def unrecorded_usd(self) -> Decimal:
        return self._unrecorded

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
            # Carried forward so the cap cannot forget it. Without this the guard's
            # next cache refresh re-reads the table, cannot see the row that was never
            # written, and spend appears to drop — so the breaker stops breaking exactly
            # when the database is already unhealthy.
            self._unrecorded += entry.cost_usd
            logger.exception(
                "llm_call_ledger_write_failed",
                profile=entry.profile,
                task=entry.task,
                cost_usd=str(entry.cost_usd),
                unrecorded_total_usd=str(self._unrecorded),
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


class _WindowedGuard[Entry: (SpendRecord, SearchSpendRecord)]:
    """The spend accounting both caps share: window → cached total → verdict → record.

    Only two things differ between the LLM and the SERP guard, and both are *policy*: where
    the window starts, and what to do once the cap is reached. Those are the two overrides.
    The accounting underneath is one implementation because the subtle parts — carrying
    unrecorded spend, invalidating the cache at a window boundary, folding a fresh record
    into the cached total — are exactly the parts that silently stop a cap from capping when
    a second copy of them drifts.
    """

    def __init__(
        self,
        *,
        ledger: _AnySpendLedger[Entry],
        cap_usd: Decimal,
        timezone: str,
        cache_ttl_s: float = 5.0,
    ) -> None:
        self._ledger = ledger
        self._cap = Decimal(cap_usd)
        self._tz = ZoneInfo(timezone)
        self._cache_ttl = timedelta(seconds=cache_ttl_s)
        self._cached_spend: Decimal | None = None
        self._cached_at: datetime | None = None

    @property
    def cap_usd(self) -> Decimal:
        return self._cap

    def window_start(self, *, now: datetime | None = None) -> datetime:
        """Start of the current spend window, in the tenant's local time."""
        raise NotImplementedError

    def _verdict(self, spent: Decimal) -> BudgetVerdict:
        """What to do now that `spent` is known. `ALLOW` below the cap, policy above it."""
        raise NotImplementedError

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

        # Spend the ledger could not persist is added on top, never subtracted. A failed
        # write must not make the cap forget money that was actually spent.
        spent = self._cached_spend + self._ledger.unrecorded_usd
        return BudgetStatus(
            verdict=self._verdict(spent),
            spent_usd=spent,
            cap_usd=self._cap,
            window_start=start,
        )

    async def record(self, entry: Entry) -> None:
        await self._ledger.record(entry)
        # Fold into the cached total so a burst inside one TTL window still trips the cap.
        if self._cached_spend is not None:
            self._cached_spend += entry.cost_usd


class BudgetGuard(_WindowedGuard[SpendRecord]):
    """Daily LLM cap. Decides whether the next paid call may happen, and records its cost."""

    def __init__(
        self,
        *,
        ledger: SpendLedger,
        daily_cap_usd: Decimal,
        on_exceeded: OnBudgetExceeded,
        timezone: str,
        cache_ttl_s: float = 5.0,
    ) -> None:
        super().__init__(
            ledger=ledger, cap_usd=daily_cap_usd, timezone=timezone, cache_ttl_s=cache_ttl_s
        )
        self._on_exceeded = on_exceeded

    @property
    def ledger(self) -> SpendLedger:
        return self._ledger

    def window_start(self, *, now: datetime | None = None) -> datetime:
        """Local midnight in the tenant's timezone, as an aware UTC-comparable instant."""
        moment = (now or datetime.now(UTC)).astimezone(self._tz)
        return moment.replace(hour=0, minute=0, second=0, microsecond=0)

    def _verdict(self, spent: Decimal) -> BudgetVerdict:
        if spent < self._cap:
            return BudgetVerdict.ALLOW
        if self._on_exceeded is OnBudgetExceeded.DEGRADE_TO_FIXTURE:
            return BudgetVerdict.DEGRADE
        return BudgetVerdict.HALT

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


# ============================================================== search (SERP) spend


@dataclass(frozen=True, slots=True)
class SearchSpendRecord:
    """One completed (or failed, or cache-served, or blocked) search query.

    Cache hits are recorded at `cost_usd = 0` rather than skipped. A cache whose hit rate
    nobody measures is an assumption, and the SERP cache exists specifically because tier-A
    templates repeat every run — the number is the evidence that it works.
    """

    provider: str
    adapter: str
    query: str
    query_hash: str
    page: int
    num: int
    results_count: int
    credits: int | None
    cost_usd: Decimal
    cost_is_estimated: bool
    latency_ms: int
    cached: bool
    outcome: SearchOutcome
    template_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    correlation_id: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SearchSpendLedger(Protocol):
    async def record(self, entry: SearchSpendRecord) -> None:
        """Append one query. Must not raise for a recoverable storage problem."""
        ...

    async def spend_since(self, since: datetime) -> Decimal: ...

    @property
    def unrecorded_usd(self) -> Decimal:
        """Spend this process knows about but could not persist. See `SpendLedger`."""
        ...


class InMemorySearchSpendLedger(_InMemoryLedger[SearchSpendRecord]):
    """SERP spend, in process."""


class PostgresSearchSpendLedger:
    """Durable SERP ledger backed by `engine_v1.search_call`."""

    def __init__(self, tenant_id: UUID) -> None:
        self._tenant_id = tenant_id
        self._unrecorded = Decimal(0)

    @property
    def unrecorded_usd(self) -> Decimal:
        return self._unrecorded

    async def record(self, entry: SearchSpendRecord) -> None:
        from v1.models import SearchCall
        from v1.platform.db import session_scope

        try:
            async with session_scope(str(self._tenant_id)) as session:
                session.add(
                    SearchCall(
                        tenant_id=self._tenant_id,
                        created_at=entry.at,
                        provider=entry.provider,
                        adapter=entry.adapter,
                        query=entry.query[:2000],
                        query_hash=entry.query_hash,
                        template_id=entry.template_id,
                        page=entry.page,
                        num=entry.num,
                        results_count=entry.results_count,
                        credits=entry.credits,
                        cost_usd=entry.cost_usd,
                        cost_is_estimated=entry.cost_is_estimated,
                        latency_ms=entry.latency_ms,
                        cached=entry.cached,
                        outcome=str(entry.outcome),
                        error_type=entry.error_type,
                        error_message=entry.error_message,
                        correlation_id=entry.correlation_id,
                    )
                )
        except Exception:
            self._unrecorded += entry.cost_usd
            logger.exception(
                "search_call_ledger_write_failed",
                provider=entry.provider,
                query_hash=entry.query_hash,
                cost_usd=str(entry.cost_usd),
                unrecorded_total_usd=str(self._unrecorded),
                outcome=str(entry.outcome),
            )

    async def spend_since(self, since: datetime) -> Decimal:
        from v1.models import SearchCall
        from v1.platform.db import session_scope

        async with session_scope(str(self._tenant_id)) as session:
            stmt = select(func.coalesce(func.sum(SearchCall.cost_usd), 0)).where(
                SearchCall.tenant_id == self._tenant_id, SearchCall.created_at >= since
            )
            total = await session.scalar(stmt)
        return Decimal(total or 0)


class SearchBudgetGuard(_WindowedGuard[SearchSpendRecord]):
    """Monthly SERP cap. A sibling policy on the shared accounting, not a mode of `BudgetGuard`.

    The window differs (month, not day) and so does what `degrade` *means*:
    `degrade_to_fixture` swaps the LLM provider, `degrade_to_focused_only` skips general
    discovery altogether. Those two differences are all that is overridden below — folding
    them into one class with a flag would put both policies in one branch, which is why
    `check` stays separate rather than being generalised.
    """

    def __init__(
        self,
        *,
        ledger: SearchSpendLedger,
        monthly_cap_usd: Decimal,
        on_exceeded: OnSearchBudgetExceeded,
        timezone: str,
        cache_ttl_s: float = 5.0,
    ) -> None:
        super().__init__(
            ledger=ledger, cap_usd=monthly_cap_usd, timezone=timezone, cache_ttl_s=cache_ttl_s
        )
        self._on_exceeded = on_exceeded

    @property
    def ledger(self) -> SearchSpendLedger:
        return self._ledger

    @property
    def on_exceeded(self) -> OnSearchBudgetExceeded:
        return self._on_exceeded

    def window_start(self, *, now: datetime | None = None) -> datetime:
        """Midnight on the 1st of the tenant's local month."""
        moment = (now or datetime.now(UTC)).astimezone(self._tz)
        return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _verdict(self, spent: Decimal) -> BudgetVerdict:
        if spent < self._cap:
            return BudgetVerdict.ALLOW
        if self._on_exceeded is OnSearchBudgetExceeded.DEGRADE_TO_FOCUSED_ONLY:
            return BudgetVerdict.DEGRADE
        return BudgetVerdict.HALT

    async def check(self, *, provider: str, now: datetime | None = None) -> BudgetStatus:
        """Raise `BudgetExceeded` unless the query may proceed.

        Both non-allow verdicts raise here, unlike the LLM guard. There is no "quietly use
        a cheaper search provider" — `degrade_to_focused_only` means *stop searching*, and
        the caller that knows how to skip general discovery is the discovery runner, which
        catches this. Letting the query through on a degrade verdict would be spending past
        the cap under a name that sounds like restraint.
        """
        status = await self.status(now=now)
        if status.verdict is BudgetVerdict.ALLOW:
            return status

        action = (
            "halt"
            if status.verdict is BudgetVerdict.HALT
            else str(OnSearchBudgetExceeded.DEGRADE_TO_FOCUSED_ONLY)
        )
        logger.error(
            "search_budget_exceeded",
            provider=provider,
            spent_usd=str(status.spent_usd),
            cap_usd=str(status.cap_usd),
            window_start=status.window_start.isoformat(),
            action=action,
        )
        raise BudgetExceeded(
            f"monthly search budget of ${status.cap_usd} reached "
            f"(spent ${status.spent_usd} since {status.window_start.isoformat()})",
            spent_usd=float(status.spent_usd),
            cap_usd=float(status.cap_usd),
            window="month",
            provider=provider,
            action=action,
        )

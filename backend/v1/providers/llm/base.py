"""The shared call funnel every LLM adapter runs through.

An adapter implements exactly one thing — `_invoke`: take a prepared call, put it on the
wire, hand back raw text plus token counts. Everything that affects *what the engine
trusts* happens here instead, once:

    budget check → spotlighted prompt → invoke (with transport retries)
                 → parse → validate → one repair attempt → typed failure
                 → ledger row (always, in `finally`)

That single funnel is what makes swapping providers safe. If each adapter had its own
retry policy and its own idea of what to do with unparseable output, "switch to a local
model" would silently change what gets published — which per ADR-005 is a
trust-affecting decision and therefore belongs in our code, not in a vendor default.

Invariants enforced here, not by convention:

* A timeout is a typed error. It is never an empty object.
* A schema failure after the repair attempt is a typed error. It is never a partial object.
* Every call writes a ledger row, including failures and budget-blocked calls.
* Untrusted content is always nonce-delimited; a caller cannot opt out.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from time import perf_counter
from typing import Any, ClassVar, Literal, TypeVar

from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from v1.config.schema import LLMProfileConfig
from v1.contracts.errors import (
    BudgetExceeded,
    LiveSpendNotPermitted,
    ProviderError,
    SchemaValidationFailed,
)
from v1.contracts.llm import (
    CallOutcome,
    LLMCapabilities,
    LLMResult,
    RawCompletion,
    StructuredOutputStrategy,
    TokenUsage,
)
from v1.platform.budget import BudgetGuard, BudgetVerdict, SpendRecord
from v1.platform.logging import correlation_id, get_logger
from v1.providers.llm.prompting import (
    build_repair_prompt,
    build_system_prompt,
    build_user_prompt,
    extract_json_object,
    new_nonce,
    schema_json_for_prompt,
)

T = TypeVar("T", bound=BaseModel)

logger = get_logger("v1.llm")

MAX_SCHEMA_ATTEMPTS = 2
"""One normal attempt plus one repair attempt. Not configurable on purpose: an unbounded
repair loop is an unbounded bill, and by the third attempt the answer is that this
provider cannot do this schema."""

# Rough characters-per-token for the estimate used when a provider reports no usage.
_CHARS_PER_TOKEN = 4

# Strategies where the provider is not told the schema out-of-band, so it goes in the prompt.
_NEEDS_SCHEMA_IN_PROMPT = frozenset(
    {StructuredOutputStrategy.JSON_OBJECT, StructuredOutputStrategy.PROMPT_ONLY}
)


@dataclass(frozen=True, slots=True)
class Turn:
    role: Literal["user", "assistant"]
    content: str


@dataclass(slots=True)
class _CallMeter:
    """Mutable running total of what a call has consumed so far.

    Deliberately mutable and passed by reference into `_run_attempts`, because the numbers
    have to survive an exception. Returning them only on success loses them exactly when
    they matter: a rate limit or timeout on the *repair* attempt happens after the first
    attempt's tokens were already billed, and reading a returned value that was never
    assigned would post that call to the ledger as `tokens=0, cost=$0`. The breaker would
    then undercount precisely on the calls that went wrong.
    """

    usage: TokenUsage = field(default_factory=TokenUsage)
    attempts: int = 0

    def add(self, usage: TokenUsage) -> None:
        self.usage = self.usage + usage


@dataclass(frozen=True, slots=True)
class WireCall:
    """A prepared call, in provider-neutral form. Adapters translate, never re-decide."""

    system: str
    turns: list[Turn]
    schema: type[BaseModel]
    schema_json: dict[str, Any]
    strategy: StructuredOutputStrategy
    temperature: float
    max_output_tokens: int
    timeout_s: float


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, ProviderError) and exc.retryable


class BaseLLMProvider(ABC):
    """Implements the `LLMProvider` port; subclasses implement `_invoke` only."""

    adapter: ClassVar[str]

    def __init__(
        self,
        *,
        name: str,
        profile: LLMProfileConfig,
        api_key: str | None = None,
        budget: BudgetGuard | None = None,
        fallback: BaseLLMProvider | None = None,
        allow_live: bool = False,
    ) -> None:
        self.name = name
        self.profile = profile
        self.model = profile.model or f"<{self.adapter}>"
        self._api_key = api_key
        self._budget = budget
        self._fallback = fallback
        self._allow_live = allow_live
        self.capabilities = LLMCapabilities(
            structured_output=profile.structured_output,
            supports_tools=profile.supports_tools,
            max_output_tokens=profile.max_output_tokens,
            is_local=profile.is_local,
        )

    # ------------------------------------------------------------------ subclass API

    @abstractmethod
    async def _invoke(self, call: WireCall) -> RawCompletion:
        """Put one prepared call on the wire.

        Must raise the typed `ProviderError` subclasses — `RateLimited` for 429/quota,
        `ProviderTimeout` for timeouts, `ProviderUnavailable` for 5xx and connection
        failures, `ProviderAuthError` for 401/403 — so the funnel can decide what is worth
        retrying. Must not parse or validate.
        """

    async def aclose(self) -> None:
        """Release transport resources. Overridden by adapters that hold a client."""
        return

    def set_fallback(self, provider: BaseLLMProvider | None) -> None:
        """Wired by the registry for `on_exceeded: degrade_to_fixture`."""
        self._fallback = provider

    # ---------------------------------------------------------------------- the port

    async def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        task: str,
        untrusted_content: str | None = None,
    ) -> LLMResult[T]:
        started = perf_counter()
        meter = _CallMeter()
        outcome = CallOutcome.PROVIDER_ERROR
        error_type: str | None = None
        error_message: str | None = None
        delegated = False

        try:
            degraded = await self._maybe_degrade(task=task)
            if degraded is not None:
                # The fallback writes its own ledger row; writing one here too would
                # double-count a call this provider never made.
                delegated = True
                return await degraded.complete_structured(
                    system=system,
                    user=user,
                    schema=schema,
                    task=task,
                    untrusted_content=untrusted_content,
                )

            self._assert_live_spend_permitted()

            result_value, raw_text, outcome = await self._run_attempts(
                system=system,
                user=user,
                schema=schema,
                untrusted_content=untrusted_content,
                meter=meter,
            )
            cost, estimated = self._price(meter.usage)
            return LLMResult[schema](  # type: ignore[valid-type]
                value=result_value,
                raw=raw_text,
                task=task,
                schema_name=schema.__name__,
                profile=self.name,
                adapter=self.adapter,
                model=self.model,
                strategy=self.capabilities.structured_output,
                usage=meter.usage,
                cost_usd=cost,
                cost_is_estimated=estimated,
                latency_ms=int((perf_counter() - started) * 1000),
                attempts=meter.attempts,
                outcome=outcome,
            )
        except SchemaValidationFailed as exc:
            outcome = CallOutcome.SCHEMA_FAILED
            error_type, error_message = exc.code, exc.message
            raise
        except LiveSpendNotPermitted as exc:
            outcome = CallOutcome.SPEND_NOT_PERMITTED
            error_type, error_message = exc.code, exc.message
            raise
        except BudgetExceeded as exc:
            outcome = CallOutcome.BUDGET_BLOCKED
            error_type, error_message = exc.code, exc.message
            raise
        except Exception as exc:
            outcome = CallOutcome.PROVIDER_ERROR
            error_type = getattr(exc, "code", type(exc).__name__)
            error_message = str(exc)[:2000]
            raise
        finally:
            # Bookkeeping happens on every path except delegation. A failed paid call still
            # cost money, and a provider whose repair rate is climbing is only visible if
            # failures are on the ledger too. `meter` carries whatever was consumed before
            # the failure, so a 429 on the repair attempt still bills the first attempt.
            if not delegated:
                cost, estimated = self._price(meter.usage)
                await self._record(
                    task=task,
                    schema_name=schema.__name__,
                    usage=meter.usage,
                    cost=cost,
                    estimated=estimated,
                    latency_ms=int((perf_counter() - started) * 1000),
                    attempts=meter.attempts,
                    outcome=outcome,
                    error_type=error_type,
                    error_message=error_message,
                )

    async def invoke_for_recording(self, call: WireCall, *, task: str) -> RawCompletion:
        """One accounted wire call, for the fixture recorder.

        The recorder needs the raw completion for a call the funnel has already prepared, so
        it cannot go through `complete_structured` (that would re-wrap the prompt and re-run
        validation). But it must not reach `_invoke` directly either: that path skips the
        budget check and writes no ledger row, so a `record_from` profile would spend real
        money invisibly and the daily cap would never see it. This is the middle: budget
        gate, transport retries, ledger row attributed to *this* (the live) profile, and no
        parsing.
        """
        started = perf_counter()
        meter = _CallMeter()
        outcome = CallOutcome.PROVIDER_ERROR
        error_type: str | None = None
        error_message: str | None = None

        try:
            self._assert_live_spend_permitted()

            if self._budget is not None and self.profile.is_paid:
                status = await self._budget.check(profile=self.name, task=task)
                if status.verdict is BudgetVerdict.DEGRADE:
                    # Degrading a *recording* to a fixture would record nothing real.
                    raise BudgetExceeded(
                        "budget exceeded; refusing to record a fixture from a live provider",
                        spent_usd=float(status.spent_usd),
                        cap_usd=float(status.cap_usd),
                        window="day",
                        profile=self.name,
                    )
            meter.attempts = 1
            raw = await self._invoke_with_retries(call)
            meter.add(self._ensure_usage(raw, call.system, call.turns))
            outcome = CallOutcome.OK
            return raw
        except LiveSpendNotPermitted as exc:
            outcome = CallOutcome.SPEND_NOT_PERMITTED
            error_type, error_message = exc.code, exc.message
            raise
        except BudgetExceeded as exc:
            outcome = CallOutcome.BUDGET_BLOCKED
            error_type, error_message = exc.code, exc.message
            raise
        except Exception as exc:
            error_type = getattr(exc, "code", type(exc).__name__)
            error_message = str(exc)[:2000]
            raise
        finally:
            cost, estimated = self._price(meter.usage)
            await self._record(
                task=task,
                schema_name=call.schema.__name__,
                usage=meter.usage,
                cost=cost,
                estimated=estimated,
                latency_ms=int((perf_counter() - started) * 1000),
                attempts=meter.attempts,
                outcome=outcome,
                error_type=error_type,
                error_message=error_message,
            )

    # ----------------------------------------------------------------- funnel pieces

    def _assert_live_spend_permitted(self) -> None:
        """Refuse to spend real money unless this process explicitly opted in.

        A resolved API key is **not** consent. Keys live in shared `.env` files and are
        inherited by every shell, test runner and container on the machine. This is the
        switch that has to be set deliberately (`V1_LLM_ALLOW_LIVE`).

        Checked *after* the degrade decision — degrading to a fixture costs nothing and
        should still work — and *before* the budget check, because "this process may not
        spend at all" is a stronger statement than "the cap is reached".
        """
        if not self.profile.is_paid or self._allow_live:
            return
        raise LiveSpendNotPermitted(
            f"LLM profile {self.name!r} ({self.adapter}, {self.model}) costs money and "
            "V1_LLM_ALLOW_LIVE is not set. A configured API key is not consent to spend "
            "it. Set V1_LLM_ALLOW_LIVE=1 to permit live calls, or run with "
            "V1_LLM_FORCE_PROFILE pointing at a local or fixture profile",
            profile=self.name,
            adapter=self.adapter,
        )

    async def _maybe_degrade(self, *, task: str) -> BaseLLMProvider | None:
        """Budget gate. Returns a fallback provider when the policy is to degrade."""
        if self._budget is None or not self.profile.is_paid:
            return None
        status = await self._budget.check(profile=self.name, task=task)
        if status.verdict is not BudgetVerdict.DEGRADE:
            return None
        if self._fallback is None:
            # Config validation requires a degrade_profile, so this means the registry was
            # built by hand. Halting is the safe reading of "over budget".
            raise BudgetExceeded(
                "budget exceeded and no fallback provider is wired",
                spent_usd=float(status.spent_usd),
                cap_usd=float(status.cap_usd),
                window="day",
                profile=self.name,
            )
        return self._fallback

    async def _run_attempts(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        untrusted_content: str | None,
        meter: _CallMeter,
    ) -> tuple[T, str, CallOutcome]:
        nonce = new_nonce()
        schema_json = schema_json_for_prompt(schema)
        system_prompt = build_system_prompt(
            system,
            nonce=nonce if untrusted_content else None,
            schema_json=schema_json,
            include_schema=self.capabilities.structured_output in _NEEDS_SCHEMA_IN_PROMPT,
        )
        user_prompt = build_user_prompt(user, untrusted_content=untrusted_content, nonce=nonce)

        turns = [Turn("user", user_prompt)]
        last_raw = ""
        last_error = "no attempt completed"

        while meter.attempts < MAX_SCHEMA_ATTEMPTS:
            meter.attempts += 1
            call = WireCall(
                system=system_prompt,
                turns=list(turns),
                schema=schema,
                schema_json=schema_json,
                strategy=self.capabilities.structured_output,
                temperature=self.profile.temperature,
                max_output_tokens=self.profile.max_output_tokens,
                timeout_s=self.profile.timeout_s,
            )
            raw = await self._invoke_with_retries(call)
            meter.add(self._ensure_usage(raw, system_prompt, turns))
            last_raw = raw.text

            try:
                value = self._parse_and_validate(raw, schema)
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_error = self._describe_validation_error(exc)
                logger.warning(
                    "llm_schema_attempt_failed",
                    profile=self.name,
                    adapter=self.adapter,
                    schema=schema.__name__,
                    attempt=meter.attempts,
                    strategy=str(self.capabilities.structured_output),
                    error=last_error[:500],
                )
                if meter.attempts >= MAX_SCHEMA_ATTEMPTS:
                    break
                turns = [
                    Turn("user", user_prompt),
                    Turn("assistant", raw.text),
                    Turn("user", build_repair_prompt(raw.text, last_error)),
                ]
                continue

            outcome = CallOutcome.OK if meter.attempts == 1 else CallOutcome.OK_REPAIRED
            return value, last_raw, outcome

        raise SchemaValidationFailed(
            f"{self.adapter} profile {self.name!r} could not produce a valid "
            f"{schema.__name__} in {meter.attempts} attempts",
            schema_name=schema.__name__,
            attempts=meter.attempts,
            validation_error=last_error,
            raw_excerpt=last_raw[:1000],
            profile=self.name,
            strategy=str(self.capabilities.structured_output),
        )

    async def _invoke_with_retries(self, call: WireCall) -> RawCompletion:
        """Transport-level retries only.

        Retries rate limits, timeouts and 5xx with jittered exponential backoff. Does *not*
        retry auth errors (a bad key stays bad) and does *not* retry schema failures — those
        get the single repair attempt instead, which is a different thing with a different
        cost.
        """
        attempt_number = 0
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.profile.max_retries + 1),
            wait=wait_exponential_jitter(initial=0.5, max=8.0, jitter=0.5),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        ):
            with attempt:
                attempt_number += 1
                if attempt_number > 1:
                    logger.info(
                        "llm_transport_retry",
                        profile=self.name,
                        adapter=self.adapter,
                        attempt=attempt_number,
                    )
                return await self._invoke(call)
        raise AssertionError("unreachable: AsyncRetrying always returns or raises")

    def _parse_and_validate(self, raw: RawCompletion, schema: type[T]) -> T:
        """One parse path for every strategy, so provider swaps cannot change semantics."""
        if raw.parsed is not None:
            return schema.model_validate(raw.parsed)
        text = raw.text
        if self.capabilities.structured_output in {
            StructuredOutputStrategy.JSON_OBJECT,
            StructuredOutputStrategy.PROMPT_ONLY,
        }:
            text = extract_json_object(text)
        if not text.strip():
            raise ValueError("provider returned an empty response body")
        return schema.model_validate_json(text)

    @staticmethod
    def _describe_validation_error(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            return "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
                for err in exc.errors()[:10]
            )
        return f"{type(exc).__name__}: {exc}"

    def _ensure_usage(
        self, raw: RawCompletion, system_prompt: str, turns: list[Turn]
    ) -> TokenUsage:
        """Use reported usage; estimate (and flag it) when the provider reports none."""
        if raw.usage.reported and raw.usage.total_tokens > 0:
            return raw.usage
        prompt_chars = len(system_prompt) + sum(len(turn.content) for turn in turns)
        return TokenUsage(
            input_tokens=prompt_chars // _CHARS_PER_TOKEN,
            output_tokens=len(raw.text) // _CHARS_PER_TOKEN,
            reported=False,
        )

    def _price(self, usage: TokenUsage) -> tuple[Decimal, bool]:
        """Cost in USD, plus whether it rests on estimated token counts.

        Prices come from config with a `source` and an `as_of` date; config validation
        refuses to load a paid profile without them, so there is no code path where a paid
        call is priced at zero by omission.
        """
        pricing = self.profile.pricing
        if pricing is None:
            return Decimal(0), False
        million = Decimal(1_000_000)
        cost = (
            Decimal(usage.input_tokens) / million * pricing.input_usd_per_mtok
            + Decimal(usage.output_tokens) / million * pricing.output_usd_per_mtok
        )
        return cost.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP), not usage.reported

    async def _record(
        self,
        *,
        task: str,
        schema_name: str,
        usage: TokenUsage,
        cost: Decimal,
        estimated: bool,
        latency_ms: int,
        attempts: int,
        outcome: CallOutcome,
        error_type: str | None,
        error_message: str | None,
    ) -> None:
        logger.info(
            "llm_call",
            profile=self.name,
            adapter=self.adapter,
            model=self.model,
            task=task,
            schema=schema_name,
            strategy=str(self.capabilities.structured_output),
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            tokens_reported=usage.reported,
            cost_usd=str(cost),
            latency_ms=latency_ms,
            attempts=attempts,
            outcome=str(outcome),
            error_type=error_type,
        )
        if self._budget is None:
            return
        await self._budget.record(
            SpendRecord(
                task=task,
                profile=self.name,
                adapter=self.adapter,
                model=self.model,
                schema_name=schema_name,
                strategy=str(self.capabilities.structured_output),
                tokens_in=usage.input_tokens,
                tokens_out=usage.output_tokens,
                cost_usd=cost,
                cost_is_estimated=estimated,
                latency_ms=latency_ms,
                attempts=attempts,
                outcome=outcome,
                error_type=error_type,
                error_message=error_message,
                correlation_id=correlation_id(),
            )
        )

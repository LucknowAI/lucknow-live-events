"""Shared vocabulary for the LLM port.

These types are what every module exchanges with the LLM layer. No vendor names,
no SDK imports — a module that speaks these types cannot tell which provider answered.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class StructuredOutputStrategy(StrEnum):
    """How a provider is asked to produce schema-conformant JSON.

    Ordered strongest → weakest. `enforces_schema` below is the load-bearing
    distinction: with the first strategy the provider constrains decoding, so invalid
    JSON is mechanically impossible; with the rest we are validating hopefully-good
    output client-side and may need the repair attempt.
    """

    NATIVE_SCHEMA = "native_schema"
    """Provider hard-enforces the JSON Schema (constrained decoding / grammar)."""

    JSON_SCHEMA_BEST_EFFORT = "json_schema_best_effort"
    """Schema is sent but not enforced — Groq's non-`strict` models, mixed gateways."""

    JSON_OBJECT = "json_object"
    """JSON mode only: syntactically valid JSON, no schema awareness."""

    TOOL_CALL = "tool_call"
    """One forced function call whose parameters are the schema."""

    PROMPT_ONLY = "prompt_only"
    """Schema described in the prompt; first JSON block is extracted."""


# Strategies where the provider itself guarantees schema conformance.
_ENFORCING = frozenset({StructuredOutputStrategy.NATIVE_SCHEMA})


class CallOutcome(StrEnum):
    """Terminal outcome of one `complete_structured` call, recorded on the ledger."""

    OK = "ok"
    OK_REPAIRED = "ok_repaired"
    SCHEMA_FAILED = "schema_failed"
    PROVIDER_ERROR = "provider_error"
    BUDGET_BLOCKED = "budget_blocked"
    SPEND_NOT_PERMITTED = "spend_not_permitted"
    """The process never opted in to live spend, so the call was not made."""


class LLMCapabilities(BaseModel):
    """What an adapter/profile combination can actually do.

    Declared per profile and validated at startup against a known-provider matrix, so a
    profile cannot silently claim hard schema enforcement it does not have (see the
    Groq `strict` finding in the Phase 1 design doc).
    """

    model_config = ConfigDict(frozen=True)

    structured_output: StructuredOutputStrategy
    supports_tools: bool = False
    supports_system_role: bool = True
    max_output_tokens: int = Field(gt=0)
    is_local: bool = False
    """True for runtimes on localhost — cost is zero and no key is needed."""

    @property
    def enforces_schema(self) -> bool:
        return self.structured_output in _ENFORCING


class TokenUsage(BaseModel):
    """Token counts as reported by the provider, summed across attempts.

    `reported` is False when the provider gave us no usage numbers and these are
    estimates — cost derived from estimated tokens is flagged as estimated.
    """

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0, default=0)
    output_tokens: int = Field(ge=0, default=0)
    reported: bool = True

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reported=self.reported and other.reported,
        )


class RawCompletion(BaseModel):
    """What an adapter returns from one wire call. Adapters do no parsing or validation."""

    model_config = ConfigDict(frozen=True)

    text: str
    """The raw response body/candidate text, exactly as received."""

    usage: TokenUsage = TokenUsage()
    parsed: dict | None = None
    """Pre-parsed object when the SDK already did it (Gemini `response.parsed`)."""


class LLMResult[T: BaseModel](BaseModel):
    """A validated structured completion plus everything needed to audit it."""

    model_config = ConfigDict(frozen=True)

    value: T
    raw: str
    """Raw text of the final (successful) attempt. Truncated by callers for logging."""

    task: str
    schema_name: str
    profile: str
    adapter: str
    model: str
    strategy: StructuredOutputStrategy

    usage: TokenUsage
    cost_usd: Decimal
    cost_is_estimated: bool
    latency_ms: int
    attempts: int
    outcome: CallOutcome

    @property
    def was_repaired(self) -> bool:
        return self.outcome is CallOutcome.OK_REPAIRED

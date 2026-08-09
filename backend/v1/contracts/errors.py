"""Typed error hierarchy for the V1 engine.

Every failure the engine can produce has a class here. Modules raise these; the API
layer maps them to HTTP status codes in exactly one place. Nothing in the engine may
swallow an exception and return an empty result instead — a caller must always be able
to tell "no data" from "we failed to get data".
"""

from __future__ import annotations

from typing import Any


class EngineError(Exception):
    """Base for every error the V1 engine raises deliberately.

    `code` is a stable machine-readable string (safe to assert on in tests and to
    branch on in clients). `context` is structured detail for logs — it must never
    contain secrets.
    """

    code = "engine_error"
    http_status = 500

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "context": self.context}

    def __str__(self) -> str:  # pragma: no cover - trivial
        if not self.context:
            return self.message
        detail = " ".join(f"{k}={v!r}" for k, v in sorted(self.context.items()))
        return f"{self.message} ({detail})"


# --------------------------------------------------------------------------- config


class ConfigError(EngineError):
    """The instance configuration is invalid. Always fatal at startup."""

    code = "config_invalid"
    http_status = 500


class SecretMissing(ConfigError):
    """A profile referenced a secret that could not be resolved from env or file."""

    code = "secret_missing"


class SchemaNotRegistered(EngineError):
    """A caller asked for a response schema that is not on the allowlist."""

    code = "schema_not_registered"
    http_status = 400


# ------------------------------------------------------------------------- provider


class ProviderError(EngineError):
    """A provider call failed for a reason attributable to the provider or transport."""

    code = "provider_error"
    http_status = 502
    retryable = False


class RateLimited(ProviderError):
    """HTTP 429 / provider quota error. Retryable, and the trigger for failover."""

    code = "provider_rate_limited"
    http_status = 429
    retryable = True

    def __init__(self, message: str, *, retry_after_s: float | None = None, **context: Any) -> None:
        super().__init__(message, **context)
        self.retry_after_s = retry_after_s


class ProviderTimeout(ProviderError):
    """The provider did not answer within the profile's timeout. Retryable."""

    code = "provider_timeout"
    http_status = 504
    retryable = True


class ProviderUnavailable(ProviderError):
    """5xx, connection error, or a local runtime that is not listening. Retryable."""

    code = "provider_unavailable"
    http_status = 502
    retryable = True


class ProviderAuthError(ProviderError):
    """401/403 — a bad or missing key. Never retryable; retrying just burns time."""

    code = "provider_auth_error"
    http_status = 502
    retryable = False


class ProviderRefused(ProviderError):
    """The provider returned no candidate content (safety block, empty completion)."""

    code = "provider_refused"
    http_status = 502
    retryable = False


class ProfileUnavailable(EngineError):
    """A configured profile exists but cannot be used (missing key, missing dependency)."""

    code = "profile_unavailable"
    http_status = 503


# --------------------------------------------------------------------------- search


class SearchUnsupported(ProviderError):
    """The provider cannot serve this query shape — e.g. page 2 with no pagination.

    Deliberately *not* an empty result. A provider that silently returns page 1 when asked
    for page 2 makes the novelty floor read "no new URLs, this query is mined out" while
    charging for every repeat. Naming the limitation is what lets the walker stop instead.
    """

    code = "search_unsupported"
    http_status = 400
    retryable = False


class AllProvidersExhausted(EngineError):
    """Every provider in the failover chain failed or was skipped.

    `context["attempts"]` carries the per-provider reason, so "discovery returned nothing"
    is never the whole story.
    """

    code = "all_providers_exhausted"
    http_status = 503


# --------------------------------------------------------------------------- sources


class SourceEnumerationFailed(EngineError):
    """A focused source could not be enumerated, or returned an implausible result.

    The second half matters more than the first. Bevy's API silently ignores unknown query
    parameters, so a typo'd filter returns the entire global firehose with HTTP 200 — a
    failure that looks exactly like success. An enumerator asserts its result is plausibly
    filtered and raises this rather than handing back 70,000 candidates.
    """

    code = "source_enumeration_failed"
    http_status = 502


class MalformedFeed(SourceEnumerationFailed):
    """A feed/sitemap was unparseable, oversized, or contained a DTD.

    Sitemaps are third-party XML. Python's stdlib parser blocks external entities but
    still expands internal ones, so an entity bomb is reachable; a sitemap has no
    legitimate use for a DTD, so any `<!DOCTYPE` is refused before parsing starts.
    """

    code = "malformed_feed"


# -------------------------------------------------------------------- structured out


class SchemaValidationFailed(EngineError):
    """The provider's output did not validate against the requested schema.

    Raised only after the single repair attempt has also failed. This is the typed
    failure the pipeline records as a stage failure — never a silent empty result.
    """

    code = "schema_validation_failed"
    http_status = 422

    def __init__(
        self,
        message: str,
        *,
        schema_name: str,
        attempts: int,
        validation_error: str | None = None,
        raw_excerpt: str | None = None,
        **context: Any,
    ) -> None:
        super().__init__(
            message,
            schema_name=schema_name,
            attempts=attempts,
            validation_error=validation_error,
            raw_excerpt=raw_excerpt,
            **context,
        )
        self.schema_name = schema_name
        self.attempts = attempts
        self.validation_error = validation_error
        self.raw_excerpt = raw_excerpt


# ---------------------------------------------------------------------- budget/fixt


class LiveSpendNotPermitted(EngineError):
    """A paid provider was called without the process opting in to live spend.

    Separate from `BudgetExceeded` on purpose: that one means "the cap says stop", this
    one means "this process was never allowed to spend at all". They need different
    answers — the first is a budgeting problem, the second is a missing environment
    variable — and reporting one as the other sends an operator to the wrong place.

    A resolved API key is not consent. Keys live in shared `.env` files and get inherited
    by every shell, test runner and container on the machine; a discovery run issues
    hundreds of queries with nobody watching. So spending needs a second, explicit switch
    (`V1_LLM_ALLOW_LIVE` / `V1_SEARCH_ALLOW_LIVE`) that nothing sets by accident.
    """

    code = "live_spend_not_permitted"
    http_status = 403


class BudgetExceeded(EngineError):
    """The configured spend cap is reached. The call was not made."""

    code = "budget_exceeded"
    http_status = 429

    def __init__(
        self, message: str, *, spent_usd: float, cap_usd: float, window: str, **context: Any
    ) -> None:
        super().__init__(message, spent_usd=spent_usd, cap_usd=cap_usd, window=window, **context)
        self.spent_usd = spent_usd
        self.cap_usd = cap_usd
        self.window = window


class FixtureMissing(EngineError):
    """A fixture-replay provider was asked for a call it has no recording of.

    The message carries the exact key and path so the recording can be produced.
    """

    code = "fixture_missing"
    http_status = 404

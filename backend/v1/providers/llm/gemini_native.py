"""Gemini adapter using the native `google-genai` SDK.

Kept separate from `openai_compatible` even though Gemini exposes an OpenAI-compatible
endpoint, because the native path's `response_schema` is the stricter of the two and
`06 §13` makes Gemini the reference AI implementation.

Two SDK facts shape this file:

* The SDK accepts a Pydantic class directly as `response_schema`, derives the schema, sets
  `property_ordering` from field order, and exposes `response.parsed`.
  https://googleapis.github.io/python-genai/
* The SDK's client-side transformer **rejects schemas containing `additionalProperties`**,
  even though the Gemini API has supported the keyword since Nov 2025
  (googleapis/python-genai#1815). Pydantic emits it for `extra="forbid"` models, so no
  schema reaching this port may use `extra="forbid"`. Enforced in the schema registry.

The SDK's own timeout is set *and* the call is wrapped in `asyncio.wait_for`. The wrapper
is the guarantee: `HttpOptions.timeout` is a transport-level hint, and a hung call that
never returns is exactly the failure mode a pipeline must not inherit.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, ClassVar

from pydantic import BaseModel

from v1.contracts.errors import (
    ProfileUnavailable,
    ProviderAuthError,
    ProviderError,
    ProviderRefused,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from v1.contracts.llm import RawCompletion, TokenUsage
from v1.platform.logging import get_logger
from v1.providers.llm.base import BaseLLMProvider, WireCall

logger = get_logger("v1.llm.gemini")


class GeminiNativeProvider(BaseLLMProvider):
    adapter: ClassVar[str] = "gemini_native"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ProfileUnavailable(
                "the google-genai package is not installed; "
                "`gemini_native` profiles cannot be used",
                profile=self.name,
            ) from exc
        if not self._api_key:
            raise ProfileUnavailable(
                f"profile {self.name!r} has no resolved API key", profile=self.name
            )
        self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def aclose(self) -> None:
        """Release the SDK's underlying HTTP transport.

        `google-genai` has moved its close method around between versions and has not always
        exposed one at all, so this probes for `aclose`/`close` rather than assuming. Dropping
        the reference is the part that always works; closing the socket pool is best-effort
        on top of it, and a failure here must not take down shutdown.
        """
        client, self._client = self._client, None
        if client is None:
            return
        for holder in (getattr(client, "aio", None), client):
            closer = getattr(holder, "aclose", None) or getattr(holder, "close", None)
            if closer is None:
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    await result
            except Exception:  # pragma: no cover - shutdown must not fail on cleanup
                logger.warning("gemini_client_close_failed", profile=self.name, exc_info=True)
            return

    async def _invoke(self, call: WireCall) -> RawCompletion:
        from google.genai import types

        client = self._get_client()
        config_kwargs: dict[str, Any] = {
            "system_instruction": call.system,
            "temperature": call.temperature,
            "max_output_tokens": call.max_output_tokens,
            "response_mime_type": "application/json",
            "response_schema": call.schema,
            "http_options": types.HttpOptions(timeout=int(call.timeout_s * 1000)),
        }
        if self.profile.thinking_budget is not None and hasattr(types, "ThinkingConfig"):
            # Thinking tokens are billed as output. Left unset by default so the model's own
            # default applies; set it in config when a task does not need reasoning.
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=self.profile.thinking_budget
            )

        contents = [
            types.Content(
                role="model" if turn.role == "assistant" else "user",
                parts=[types.Part.from_text(text=turn.content)],
            )
            for turn in call.turns
        ]

        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=types.GenerateContentConfig(**config_kwargs),
                ),
                # Exactly the configured deadline, no slack. `HttpOptions.timeout` is a
                # transport hint; this wrapper is the guarantee, and a wrapper that fires
                # later than the number in the config means the config is not the timeout.
                timeout=call.timeout_s,
            )
        except TimeoutError as exc:
            raise ProviderTimeout(
                f"gemini call exceeded {call.timeout_s}s", profile=self.name, model=self.model
            ) from exc
        except Exception as exc:
            raise _map_error(exc, profile=self.name, model=self.model) from exc

        text = (response.text or "").strip()
        parsed = _as_dict(getattr(response, "parsed", None))
        if not text and parsed is None:
            # A safety block or an empty candidate list. Distinct from "invalid schema" —
            # a repair attempt would be pointless, so this surfaces as a provider error.
            raise ProviderRefused(
                "gemini returned no content (safety block or empty candidate)",
                profile=self.name,
                model=self.model,
                finish_reason=_finish_reason(response),
            )
        return RawCompletion(text=text, parsed=parsed, usage=_usage(response))


def _as_dict(parsed: Any) -> dict | None:
    """Normalise `response.parsed` to a plain dict.

    The SDK hands back an instance of the schema class. We still re-validate it in the
    funnel rather than trusting it: one validation path for every provider is what makes a
    provider swap safe, and the cost of re-validating a dict is nothing.
    """
    if parsed is None:
        return None
    if isinstance(parsed, BaseModel):
        return parsed.model_dump(mode="python")
    if isinstance(parsed, dict):
        return parsed
    return None


def _finish_reason(response: Any) -> str | None:
    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return None
    return str(getattr(candidates[0], "finish_reason", None))


def _usage(response: Any) -> TokenUsage:
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return TokenUsage(reported=False)
    prompt = getattr(meta, "prompt_token_count", None) or 0
    candidates = getattr(meta, "candidates_token_count", None) or 0
    # Thinking tokens are billed as output but reported separately on 2.5+ models.
    thoughts = getattr(meta, "thoughts_token_count", None) or 0
    total_out = candidates + thoughts
    if prompt == 0 and total_out == 0:
        return TokenUsage(reported=False)
    return TokenUsage(input_tokens=prompt, output_tokens=total_out, reported=True)


def _map_error(exc: Exception, *, profile: str, model: str) -> ProviderError:
    """Map SDK/transport exceptions onto the port's typed errors.

    Status-code driven rather than message driven, with a message fallback, because the
    SDK's exception classes have moved between versions and a mapping that breaks silently
    would turn a rate limit into "unknown error" and disable failover.
    """
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    text = str(exc).lower()
    context = {"profile": profile, "model": model}

    if status == 429 or "resource_exhausted" in text or "rate limit" in text or "quota" in text:
        return RateLimited(f"gemini rate limited: {exc}", **context)
    if status in {401, 403} or "api key" in text or "permission" in text:
        return ProviderAuthError(f"gemini rejected the credentials: {exc}", **context)
    if isinstance(status, int) and status >= 500:
        return ProviderUnavailable(f"gemini server error {status}: {exc}", **context)
    if "timeout" in text or "deadline" in text:
        return ProviderTimeout(f"gemini timed out: {exc}", **context)
    if isinstance(status, int) and 400 <= status < 500:
        return ProviderError(f"gemini rejected the request ({status}): {exc}", **context)
    return ProviderUnavailable(f"gemini call failed: {exc}", **context)

"""One adapter for every OpenAI-compatible endpoint.

Covers Groq, OpenRouter, LM Studio, vLLM, DeepSeek, Together, Fireworks, Cerebras and xAI
with nothing but `base_url` + `api_key_ref` + `model` from config.

**Why this does not use `client.chat.completions.parse()`.** The SDK helper runs the
Pydantic model through `to_strict_json_schema`, which forces `strict: true`,
`additionalProperties: false`, and every field required
(https://github.com/openai/openai-python/blob/main/helpers.md). Those constraints are
exactly what the non-OpenAI backends behind this adapter do not accept:

* Groq honours `strict: true` only on `openai/gpt-oss-*`; on every other model it is
  ignored, and strict-shaped schemas with all-required fields distort the request for no
  benefit (https://console.groq.com/docs/structured-outputs).
* LM Studio wants `response_format.json_schema = {name, schema}` and does not document
  full strict parity (https://lmstudio.ai/docs/developer/openai-compat/structured-output).
* OpenRouter's support varies per *route*, not just per model, which is why
  `provider.require_parameters` exists (https://openrouter.ai/docs/features/structured-outputs).

So the request body is built here from the profile's declared strategy, and the result is
always validated by the funnel. One adapter, explicit behaviour, no hidden per-backend
divergence.
"""

from __future__ import annotations

from typing import Any, ClassVar

from v1.contracts.errors import (
    ProfileUnavailable,
    ProviderAuthError,
    ProviderError,
    ProviderRefused,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from v1.contracts.llm import RawCompletion, StructuredOutputStrategy, TokenUsage
from v1.providers.llm.base import BaseLLMProvider, WireCall

# Local runtimes require no key, but the OpenAI SDK refuses to construct a client without
# one. This placeholder is sent to localhost only and is not a secret.
LOCAL_PLACEHOLDER_KEY = "not-needed"

STRUCTURED_TOOL_NAME = "emit_structured_output"


class OpenAICompatibleProvider(BaseLLMProvider):
    adapter: ClassVar[str] = "openai_compatible"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ProfileUnavailable(
                "the openai package is not installed; `openai_compatible` profiles cannot be used",
                profile=self.name,
            ) from exc

        api_key = self._api_key
        if not api_key:
            if not self.profile.is_local:
                raise ProfileUnavailable(
                    f"profile {self.name!r} targets a remote endpoint and has no resolved API key",
                    profile=self.name,
                )
            api_key = LOCAL_PLACEHOLDER_KEY

        self._client = AsyncOpenAI(
            base_url=self.profile.base_url,
            api_key=api_key,
            timeout=self.profile.timeout_s,
            # The funnel owns retries so that backoff, jitter and which errors are worth
            # retrying are one policy across all adapters.
            max_retries=0,
        )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _response_format(self, call: WireCall) -> tuple[dict | None, list | None, dict | None]:
        """Translate the declared strategy into `response_format` / `tools` / `tool_choice`."""
        strategy = call.strategy
        schema_name = call.schema.__name__

        if strategy in {
            StructuredOutputStrategy.NATIVE_SCHEMA,
            StructuredOutputStrategy.JSON_SCHEMA_BEST_EFFORT,
        }:
            json_schema: dict[str, Any] = {"name": schema_name, "schema": call.schema_json}
            if strategy is StructuredOutputStrategy.NATIVE_SCHEMA:
                # Only claimed where the provider documents constrained decoding; config
                # validation blocks the combinations that would silently ignore it.
                json_schema["strict"] = True
            return {"type": "json_schema", "json_schema": json_schema}, None, None

        if strategy is StructuredOutputStrategy.JSON_OBJECT:
            return {"type": "json_object"}, None, None

        if strategy is StructuredOutputStrategy.TOOL_CALL:
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": STRUCTURED_TOOL_NAME,
                        "description": f"Return the extracted {schema_name}.",
                        "parameters": call.schema_json,
                    },
                }
            ]
            return None, tools, {"type": "function", "function": {"name": STRUCTURED_TOOL_NAME}}

        # PROMPT_ONLY: the schema is already in the system prompt; nothing on the wire.
        return None, None, None

    async def _invoke(self, call: WireCall) -> RawCompletion:
        client = self._get_client()
        response_format, tools, tool_choice = self._response_format(call)

        messages: list[dict[str, str]] = [{"role": "system", "content": call.system}]
        messages.extend({"role": turn.role, "content": turn.content} for turn in call.turns)

        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": call.temperature,
            "max_tokens": call.max_output_tokens,
        }
        if response_format is not None:
            body["response_format"] = response_format
        if tools is not None:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        if self.profile.extra_body:
            # Provider-specific knobs (OpenRouter's `provider.require_parameters`, vLLM's
            # `guided_decoding_backend`) without teaching this adapter about any vendor.
            body["extra_body"] = dict(self.profile.extra_body)

        try:
            completion = await client.chat.completions.create(**body)
        except Exception as exc:
            raise _map_error(exc, profile=self.name, model=self.model) from exc

        return RawCompletion(
            text=_content_of(completion, self.name),
            usage=_usage(completion),
        )


def _content_of(completion: Any, profile: str) -> str:
    choices = getattr(completion, "choices", None) or []
    if not choices:
        raise ProviderRefused("provider returned no choices", profile=profile)
    message = choices[0].message

    # tool_call strategy: the JSON lives in the call arguments, not the content.
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        arguments = getattr(tool_calls[0].function, "arguments", None)
        if arguments:
            return arguments

    content = getattr(message, "content", None)
    if content:
        return content

    finish_reason = getattr(choices[0], "finish_reason", None)
    if finish_reason == "length":
        # Truncated output is a configuration problem, not a model mistake; a repair attempt
        # would truncate identically.
        raise ProviderError(
            "provider truncated the response at max_tokens; raise max_output_tokens "
            "for this profile",
            profile=profile,
            finish_reason=finish_reason,
        )
    raise ProviderRefused(
        "provider returned an empty message", profile=profile, finish_reason=finish_reason
    )


def _usage(completion: Any) -> TokenUsage:
    usage = getattr(completion, "usage", None)
    if usage is None:
        return TokenUsage(reported=False)
    prompt = getattr(usage, "prompt_tokens", None) or 0
    output = getattr(usage, "completion_tokens", None) or 0
    if prompt == 0 and output == 0:
        return TokenUsage(reported=False)
    return TokenUsage(input_tokens=prompt, output_tokens=output, reported=True)


def _map_error(exc: Exception, *, profile: str, model: str) -> ProviderError:
    """Map the OpenAI SDK's exception surface onto the port's typed errors."""
    context = {"profile": profile, "model": model}
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    text = str(exc).lower()

    if status == 429 or name == "RateLimitError":
        retry_after = None
        headers = getattr(getattr(exc, "response", None), "headers", None)
        if headers:
            raw = headers.get("retry-after")
            if raw:
                try:
                    retry_after = float(raw)
                except ValueError:
                    retry_after = None
        return RateLimited(f"{model} rate limited: {exc}", retry_after_s=retry_after, **context)
    if status in {401, 403} or name in {"AuthenticationError", "PermissionDeniedError"}:
        return ProviderAuthError(f"{model} rejected the credentials: {exc}", **context)
    if name in {"APITimeoutError"} or "timeout" in text or "timed out" in text:
        return ProviderTimeout(f"{model} timed out: {exc}", **context)
    if name in {"APIConnectionError", "InternalServerError"} or (
        isinstance(status, int) and status >= 500
    ):
        return ProviderUnavailable(f"{model} unreachable or erroring: {exc}", **context)
    if isinstance(status, int) and 400 <= status < 500:
        # Includes "this model does not support response_format" — a config bug, and one we
        # deliberately do not retry, so it shows up loudly the first time.
        return ProviderError(f"{model} rejected the request ({status}): {exc}", **context)
    return ProviderUnavailable(f"{model} call failed: {exc}", **context)

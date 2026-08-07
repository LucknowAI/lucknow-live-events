"""Ollama adapter using the native `/api/chat` endpoint.

Ollama is deliberately *not* served by `openai_compatible`. Its
`/v1/chat/completions` still does not accept OpenAI's `response_format.json_schema`
shape (ollama/ollama#10001), while the native `/api/chat` takes a JSON Schema directly in
`format` and constrains decoding with llama.cpp GBNF grammars — which makes malformed
JSON mechanically impossible. Using the compat path would mean choosing the weaker of the
two paths for no gain.
https://docs.ollama.com/capabilities/structured-outputs

Plain `httpx` rather than the `ollama` package: the whole surface used here is one POST,
and `httpx` is already a dependency. One fewer dependency for one request shape.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx

from v1.contracts.errors import (
    ProviderError,
    ProviderRefused,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from v1.contracts.llm import RawCompletion, StructuredOutputStrategy, TokenUsage
from v1.providers.llm.base import BaseLLMProvider, WireCall


class OllamaNativeProvider(BaseLLMProvider):
    adapter: ClassVar[str] = "ollama_native"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=(self.profile.base_url or "http://localhost:11434").rstrip("/"),
                timeout=httpx.Timeout(self.profile.timeout_s),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _format_field(call: WireCall) -> dict | str | None:
        if call.strategy is StructuredOutputStrategy.NATIVE_SCHEMA:
            # The schema itself: Ollama compiles it to a grammar and constrains decoding.
            return call.schema_json
        if call.strategy is StructuredOutputStrategy.JSON_OBJECT:
            return "json"
        return None

    async def _invoke(self, call: WireCall) -> RawCompletion:
        client = self._get_client()
        messages: list[dict[str, str]] = [{"role": "system", "content": call.system}]
        messages.extend({"role": turn.role, "content": turn.content} for turn in call.turns)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": call.temperature,
                "num_predict": call.max_output_tokens,
            },
        }
        fmt = self._format_field(call)
        if fmt is not None:
            payload["format"] = fmt
        if self.profile.extra_body:
            payload.update(self.profile.extra_body)

        try:
            response = await client.post("/api/chat", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(
                f"ollama did not respond within {call.timeout_s}s",
                profile=self.name,
                model=self.model,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(
                f"could not reach ollama at {self.profile.base_url}: {exc}",
                profile=self.name,
                model=self.model,
            ) from exc

        if response.status_code == 429:
            raise RateLimited("ollama reported it is busy", profile=self.name, model=self.model)
        if response.status_code >= 500:
            raise ProviderUnavailable(
                f"ollama server error {response.status_code}: {response.text[:300]}",
                profile=self.name,
                model=self.model,
            )
        if response.status_code >= 400:
            # The common case here is "model not found" — a pull step the operator has not
            # run. Not retryable; the message needs to say what to do.
            raise ProviderError(
                f"ollama rejected the request ({response.status_code}): "
                f"{response.text[:300]}. If the model is missing, run "
                f"`ollama pull {self.model}`.",
                profile=self.name,
                model=self.model,
            )

        body = response.json()
        text = (body.get("message") or {}).get("content") or ""
        if not text.strip():
            raise ProviderRefused(
                "ollama returned an empty message",
                profile=self.name,
                model=self.model,
                done_reason=body.get("done_reason"),
            )
        return RawCompletion(text=text, usage=_usage(body))


def _usage(body: dict[str, Any]) -> TokenUsage:
    prompt = body.get("prompt_eval_count") or 0
    output = body.get("eval_count") or 0
    if prompt == 0 and output == 0:
        return TokenUsage(reported=False)
    return TokenUsage(input_tokens=int(prompt), output_tokens=int(output), reported=True)

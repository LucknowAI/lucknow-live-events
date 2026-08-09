"""Wire-level tests for the two network adapters.

The funnel tests use a stub adapter, which proves the shared logic but says nothing about
whether we put the *right bytes* on the wire. That gap matters here more than usual: the
whole point of declaring a structured-output strategy per profile is that the request body
differs between them, and a mistake there fails silently — the provider just returns
unconstrained JSON and the funnel's repair attempt papers over it at double the cost.

So these assert the actual HTTP request, with `respx` standing in for the provider. Still no
network, still free, still deterministic.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from v1.config.schema import AdapterKind, LLMProfileConfig, PricingConfig
from v1.contracts.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimited,
)
from v1.contracts.llm import StructuredOutputStrategy
from v1.providers.llm.gemini_native import GeminiNativeProvider
from v1.providers.llm.ollama_native import OllamaNativeProvider
from v1.providers.llm.openai_compatible import (
    LOCAL_PLACEHOLDER_KEY,
    STRUCTURED_TOOL_NAME,
    OpenAICompatibleProvider,
)
from v1.providers.llm.schemas import LlmSelfTestProbe

BASE_URL = "https://provider.example/v1"
COMPLETIONS = f"{BASE_URL}/chat/completions"
OLLAMA_CHAT = "http://localhost:11434/api/chat"

VALID = {"title": "Kubernetes Bootcamp", "city": "Springfield", "year": 2027, "is_free": True}
VALID_JSON = json.dumps(VALID)

PRICING = PricingConfig(
    input_usd_per_mtok=Decimal("0.25"),
    output_usd_per_mtok=Decimal("1.50"),
    source="test",
    as_of="2026-08-06",
)


def openai_profile(**overrides: Any) -> LLMProfileConfig:
    defaults: dict[str, Any] = {
        "adapter": AdapterKind.OPENAI_COMPATIBLE,
        "base_url": BASE_URL,
        "model": "stub-1",
        "api_key_ref": "STUB_KEY",
        "structured_output": StructuredOutputStrategy.NATIVE_SCHEMA,
        "max_retries": 0,
        "pricing": PRICING,
    }
    return LLMProfileConfig(**(defaults | overrides))


def openai_provider(**overrides: Any) -> OpenAICompatibleProvider:
    api_key = overrides.pop("api_key", "stub-key")
    return OpenAICompatibleProvider(
        name="wire", profile=openai_profile(**overrides), api_key=api_key, allow_live=True
    )


def chat_completion(content: str = VALID_JSON, **overrides: Any) -> dict:
    body = {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "stub-1",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150},
    }
    body.update(overrides)
    return body


async def probe(provider, **kwargs: Any):
    try:
        return await provider.complete_structured(
            system="Extract facts.",
            user="Report the fields.",
            schema=LlmSelfTestProbe,
            task="extraction",
            **kwargs,
        )
    finally:
        await provider.aclose()


# ------------------------------------------------------- openai_compatible: the body


@respx.mock
async def test_native_schema_sends_strict_json_schema() -> None:
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=chat_completion()))
    result = await probe(openai_provider())

    body = json.loads(route.calls.last.request.content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "LlmSelfTestProbe"
    # `strict` is the whole difference between the two schema strategies.
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["model"] == "stub-1"
    assert body["messages"][0]["role"] == "system"

    assert result.value.year == 2027
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 30
    # 120/1M * $0.25 + 30/1M * $1.50
    assert result.cost_usd == Decimal("0.000075")


@respx.mock
async def test_best_effort_sends_the_schema_without_strict() -> None:
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=chat_completion()))
    await probe(openai_provider(structured_output=StructuredOutputStrategy.JSON_SCHEMA_BEST_EFFORT))

    schema_block = json.loads(route.calls.last.request.content)["response_format"]["json_schema"]
    assert "strict" not in schema_block
    assert schema_block["schema"]["properties"].keys() >= {"title", "city", "year", "is_free"}


@respx.mock
async def test_json_object_mode_sends_no_schema_and_inlines_it_in_the_prompt() -> None:
    route = respx.post(COMPLETIONS).mock(
        return_value=httpx.Response(200, json=chat_completion(f"Sure!\n```json\n{VALID_JSON}\n```"))
    )
    result = await probe(openai_provider(structured_output=StructuredOutputStrategy.JSON_OBJECT))

    body = json.loads(route.calls.last.request.content)
    assert body["response_format"] == {"type": "json_object"}
    # The provider is not told the schema out of band, so it must be in the system prompt.
    assert "JSON Schema" in body["messages"][0]["content"]
    # Fenced, prose-wrapped output still parses on the weak strategies.
    assert result.value.city == "Springfield"


@respx.mock
async def test_tool_call_mode_forces_one_function_and_reads_its_arguments() -> None:
    tool_response = chat_completion(
        content=None,
        choices=[
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": STRUCTURED_TOOL_NAME,
                                "arguments": VALID_JSON,
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    )
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=tool_response))
    result = await probe(
        openai_provider(structured_output=StructuredOutputStrategy.TOOL_CALL, supports_tools=True)
    )

    body = json.loads(route.calls.last.request.content)
    assert body["tool_choice"]["function"]["name"] == STRUCTURED_TOOL_NAME
    assert body["tools"][0]["function"]["parameters"]["properties"]["year"]
    assert "response_format" not in body
    # The JSON lives in the call arguments, not in the message content.
    assert result.value.title == "Kubernetes Bootcamp"


@respx.mock
async def test_extra_body_reaches_the_wire() -> None:
    """OpenRouter's `provider.require_parameters` is what makes its native_schema claim true."""
    route = respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=chat_completion()))
    await probe(openai_provider(extra_body={"provider": {"require_parameters": True}}))

    body = json.loads(route.calls.last.request.content)
    assert body["provider"] == {"require_parameters": True}


@respx.mock
async def test_local_profile_needs_no_key() -> None:
    local = "http://localhost:1234/v1"
    route = respx.post(f"{local}/chat/completions").mock(
        return_value=httpx.Response(200, json=chat_completion())
    )
    provider = OpenAICompatibleProvider(
        name="local",
        profile=openai_profile(base_url=local, api_key_ref=None, pricing=None),
        api_key=None,
    )
    result = await probe(provider)

    assert result.cost_usd == 0  # local runtimes are free by construction
    assert route.calls.last.request.headers["authorization"] == f"Bearer {LOCAL_PLACEHOLDER_KEY}"


# ------------------------------------------------- openai_compatible: error mapping


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, RateLimited),
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (500, ProviderUnavailable),
        (503, ProviderUnavailable),
        (400, ProviderError),
    ],
)
@respx.mock
async def test_http_status_maps_to_the_right_typed_error(status: int, expected: type) -> None:
    """Failover and retry decisions are made on these types; a wrong mapping disables both."""
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(expected):
        await probe(openai_provider())


@respx.mock
async def test_connection_failure_is_unavailable_not_a_crash() -> None:
    respx.post(COMPLETIONS).mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ProviderUnavailable):
        await probe(openai_provider())


@respx.mock
async def test_transport_timeout_is_typed() -> None:
    respx.post(COMPLETIONS).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ProviderTimeout):
        await probe(openai_provider())


@respx.mock
async def test_truncation_at_max_tokens_is_a_config_error_not_a_repair() -> None:
    """A repair attempt would truncate identically, so it must not be retried as one."""
    truncated = chat_completion(
        content=None,
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": None},
                "finish_reason": "length",
            }
        ],
    )
    respx.post(COMPLETIONS).mock(return_value=httpx.Response(200, json=truncated))
    with pytest.raises(ProviderError) as exc:
        await probe(openai_provider())
    assert "max_output_tokens" in str(exc.value)


@respx.mock
async def test_rate_limit_then_success_is_retried_once_on_the_wire() -> None:
    route = respx.post(COMPLETIONS).mock(
        side_effect=[
            httpx.Response(429, json={"error": "slow down"}),
            httpx.Response(200, json=chat_completion()),
        ]
    )
    result = await probe(openai_provider(max_retries=1))
    assert route.call_count == 2
    assert result.attempts == 1  # the 429 produced no completion


# ------------------------------------------------------------------ ollama_native


def ollama_provider(**overrides: Any) -> OllamaNativeProvider:
    defaults: dict[str, Any] = {
        "adapter": AdapterKind.OLLAMA_NATIVE,
        "model": "qwen2.5:14b",
        "structured_output": StructuredOutputStrategy.NATIVE_SCHEMA,
        "max_retries": 0,
    }
    return OllamaNativeProvider(
        name="ollama", profile=LLMProfileConfig(**(defaults | overrides)), allow_live=True
    )


def ollama_response(content: str = VALID_JSON, **overrides: Any) -> dict:
    body = {
        "model": "qwen2.5:14b",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 210,
        "eval_count": 44,
    }
    body.update(overrides)
    return body


@respx.mock
async def test_ollama_sends_the_schema_in_format_not_response_format() -> None:
    """Ollama's /v1 path still rejects OpenAI's json_schema shape; the native field is the one
    that grammar-constrains decoding (ollama/ollama#10001)."""
    route = respx.post(OLLAMA_CHAT).mock(return_value=httpx.Response(200, json=ollama_response()))
    result = await probe(ollama_provider())

    body = json.loads(route.calls.last.request.content)
    assert body["stream"] is False
    assert body["format"]["properties"].keys() >= {"title", "city", "year", "is_free"}
    assert "response_format" not in body
    assert body["options"]["num_predict"] == 4096

    assert result.usage.input_tokens == 210
    assert result.usage.output_tokens == 44
    assert result.cost_usd == 0  # local, and config forbids pricing a local profile


@respx.mock
async def test_ollama_json_object_mode_sends_the_string_format() -> None:
    route = respx.post(OLLAMA_CHAT).mock(return_value=httpx.Response(200, json=ollama_response()))
    await probe(ollama_provider(structured_output=StructuredOutputStrategy.JSON_OBJECT))
    assert json.loads(route.calls.last.request.content)["format"] == "json"


@respx.mock
async def test_ollama_missing_model_tells_you_to_pull_it() -> None:
    respx.post(OLLAMA_CHAT).mock(
        return_value=httpx.Response(404, json={"error": 'model "qwen2.5:14b" not found'})
    )
    with pytest.raises(ProviderError) as exc:
        await probe(ollama_provider())
    assert "ollama pull qwen2.5:14b" in str(exc.value)


@respx.mock
async def test_ollama_not_running_is_unavailable() -> None:
    respx.post(OLLAMA_CHAT).mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(ProviderUnavailable) as exc:
        await probe(ollama_provider())
    assert "localhost:11434" in str(exc.value)


# ------------------------------------------------------------------- client cleanup


@respx.mock
async def test_ollama_client_is_released_on_close() -> None:
    respx.post(OLLAMA_CHAT).mock(return_value=httpx.Response(200, json=ollama_response()))
    provider = ollama_provider()
    await probe(provider)  # probe() closes it
    assert provider._client is None


async def test_gemini_close_releases_the_client_even_when_the_sdk_has_no_close() -> None:
    """`google-genai` has moved its close method around and has not always exposed one.

    Dropping the reference is the part that must always happen; closing the socket pool is
    best effort on top. A shutdown that raises because the SDK changed shape is worse than a
    socket that the interpreter reclaims.
    """

    class _NoCloseClient:
        pass

    class _AsyncCloseClient:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    class _RaisingClient:
        def close(self) -> None:
            raise RuntimeError("sdk changed shape")

    provider = GeminiNativeProvider(
        name="gemini",
        profile=LLMProfileConfig(
            adapter=AdapterKind.GEMINI_NATIVE,
            model="gemini-3.1-flash-lite",
            api_key_ref="GEMINI_KEY",
            pricing=PRICING,
        ),
        api_key="k",
    )

    provider._client = _NoCloseClient()
    await provider.aclose()
    assert provider._client is None

    closable = _AsyncCloseClient()
    provider._client = closable
    await provider.aclose()
    assert closable.closed is True
    assert provider._client is None

    provider._client = _RaisingClient()
    await provider.aclose()  # must not propagate
    assert provider._client is None

    await provider.aclose()  # idempotent

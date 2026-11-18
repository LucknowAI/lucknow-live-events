import json

import httpx
import pytest
from httpx import AsyncClient, MockTransport

import llm


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(llm.settings, "LLM_API_KEY", "test-only-key")
    monkeypatch.setattr(llm.settings, "LLM_BASE_URL", "https://provider.example/custom/v1/")
    monkeypatch.setattr(llm.settings, "LLM_MODEL", "test-model")

    def install(handler):
        client = AsyncClient(transport=MockTransport(handler), timeout=30)
        monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: client)
        return client

    return install


@pytest.mark.parametrize("key", ["", " \t\n"])
async def test_no_key_makes_no_request(monkeypatch, provider, key):
    monkeypatch.setattr(llm.settings, "LLM_API_KEY", key)

    def unexpected(**kwargs):
        pytest.fail("Disabled LLM must not create an HTTP client")

    monkeypatch.setattr(llm.httpx, "AsyncClient", unexpected)
    with pytest.raises(llm.LLMDisabled):
        await llm.complete("hello")


@pytest.mark.parametrize("setting", ["LLM_BASE_URL", "LLM_MODEL"])
async def test_incomplete_configuration_makes_no_request(monkeypatch, provider, setting):
    monkeypatch.setattr(llm.settings, setting, " ")
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda **kwargs: pytest.fail("No request"))
    with pytest.raises(ValueError, match="must be configured"):
        await llm.complete("hello")


@pytest.mark.parametrize("system", [None, "Be concise."])
async def test_completion_request_and_text(provider, system):
    def handle(request):
        assert str(request.url) == "https://provider.example/custom/v1/chat/completions"
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer test-only-key"
        messages = [{"role": "user", "content": "hello"}]
        if system is not None:
            messages.insert(0, {"role": "system", "content": system})
        assert json.loads(request.content) == {"model": "test-model", "messages": messages}
        return httpx.Response(200, json={"choices": [{"message": {"content": "answer"}}]})

    client = provider(handle)
    assert await llm.complete("hello", system) == "answer"
    assert client.is_closed


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_provider_error_is_not_retried_or_hidden(provider, status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status)

    client = provider(handle)
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await llm.complete("hello")
    assert exc.value.response.status_code == status and len(calls) == 1
    assert client.is_closed


async def test_timeout_propagates(provider):
    def handle(request):
        raise httpx.ReadTimeout("timed out", request=request)

    client = provider(handle)
    with pytest.raises(httpx.ReadTimeout):
        await llm.complete("hello")
    assert client.is_closed


@pytest.mark.parametrize(
    "payload", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]}]
)
async def test_missing_text_is_an_error(provider, payload):
    provider(lambda request: httpx.Response(200, json=payload))
    with pytest.raises((ValueError, TypeError), match="does not contain text"):
        await llm.complete("hello")

"""Optional text completion. No v1 engine or API code calls this helper."""

import httpx

from api.core.config import settings


class LLMDisabled(RuntimeError):
    """No provider key is configured."""


async def complete(prompt: str, system: str | None = None) -> str:
    key = settings.LLM_API_KEY.strip()
    if not key:
        raise LLMDisabled("LLM_API_KEY is not configured")
    base, model = settings.LLM_BASE_URL.strip(), settings.LLM_MODEL.strip()
    if not base or not model:
        raise ValueError("LLM_BASE_URL and LLM_MODEL must be configured")
    messages = []
    if system is not None:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": messages},
        )
        response.raise_for_status()
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ValueError("LLM response does not contain text") from exc
    if not isinstance(content, str):
        raise TypeError("LLM response does not contain text")
    return content

from __future__ import annotations

from functools import lru_cache

from api.core.config import settings


@lru_cache(maxsize=1)
def get_client():
    # Plan requires google-genai (from google import genai).
    from google import genai  # type: ignore

    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def json_config(schema: type, system_instruction: str | None = None):
    from google.genai import types  # type: ignore

    # Docs: prefer response_json_schema (JSON Schema dict) with response_mime_type="application/json".
    # This works cleanly with Pydantic via model_json_schema().
    response_json_schema = schema.model_json_schema() if hasattr(schema, "model_json_schema") else None

    return types.GenerateContentConfig(
        response_mime_type="application/json",
        response_json_schema=response_json_schema,
        system_instruction=system_instruction,
        temperature=0.1,
        max_output_tokens=2048,
    )

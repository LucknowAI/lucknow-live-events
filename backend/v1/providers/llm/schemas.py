"""The response-schema allowlist.

`POST /llm/complete` takes a **schema name**, never a caller-supplied JSON Schema. A
caller who can post an arbitrary schema can drive arbitrary output shape and arbitrary
token spend through our key, and can craft a schema whose validation always fails to force
the repair attempt on every call — doubling the bill. An allowlist removes all of that for
the price of one dict.

`validate_registry()` also enforces the Gemini constraint from
googleapis/python-genai#1815: a Pydantic model with `extra="forbid"` emits
`additionalProperties`, which the SDK's client-side transformer rejects. Caught at startup
rather than on the first extraction call.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from v1.contracts.errors import ConfigError, SchemaNotRegistered
from v1.contracts.event import ExtractedEvent


class LlmSelfTestProbe(BaseModel):
    """Tiny schema for the conformance suite.

    Small enough that even a 7B local model can satisfy it, and shaped so a correct answer
    is checkable without a fuzzy match: every field has exactly one right value for the
    golden input.
    """

    title: str = Field(min_length=1, max_length=200, description="The event's title.")
    city: str = Field(min_length=1, max_length=100, description="City where it takes place.")
    year: int = Field(ge=1900, le=2200, description="Four-digit year of the event.")
    is_free: bool = Field(description="True if attendance is free of charge.")


SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    ExtractedEvent.__name__: ExtractedEvent,
    LlmSelfTestProbe.__name__: LlmSelfTestProbe,
}


def get_schema(name: str) -> type[BaseModel]:
    try:
        return SCHEMA_REGISTRY[name]
    except KeyError as exc:
        raise SchemaNotRegistered(
            f"unknown response schema {name!r}; allowed: {sorted(SCHEMA_REGISTRY)}",
            schema_name=name,
        ) from exc


def validate_registry() -> None:
    """Startup check: every registered schema must be usable by every adapter."""
    for name, model in SCHEMA_REGISTRY.items():
        if model.model_config.get("extra") == "forbid":
            raise ConfigError(
                f"schema {name} sets extra='forbid', which makes Pydantic emit "
                "`additionalProperties`; the google-genai SDK rejects that client-side "
                "(googleapis/python-genai#1815). Remove it — the port validates strictly "
                "on our side instead."
            )
        schema_json = model.model_json_schema(mode="validation")
        if "additionalProperties" in schema_json:
            raise ConfigError(
                f"schema {name} emits `additionalProperties`, which gemini_native rejects"
            )

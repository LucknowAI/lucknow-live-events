"""Mock provider — synthesises schema-valid values with no fixtures and no network.

Complements the fixture provider rather than duplicating it:

* `fixture` proves the *content* path — a recorded real response, so the extracted values
  are the ones a real model produced.
* `mock` proves the *plumbing* path — any schema, no setup, no recording. It is what a new
  schema is smoke-tested against, and what `on_exceeded: degrade_to_fixture` falls back to
  when no recording exists for the call.

It deliberately does not try to look plausible. Values are obviously synthetic
(`mock-title`, `MOCK_CITY`) so a mock response can never be mistaken for real data in a
database or a screenshot.
"""

from __future__ import annotations

import json
import types
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar, Literal, Union, get_args, get_origin
from uuid import UUID, uuid5

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from v1.contracts.llm import RawCompletion, TokenUsage
from v1.providers.llm.base import BaseLLMProvider, WireCall

MOCK_NAMESPACE = UUID("11111111-2222-3333-4444-555555555555")
_MAX_DEPTH = 5


class MockProvider(BaseLLMProvider):
    adapter: ClassVar[str] = "mock"

    async def _invoke(self, call: WireCall) -> RawCompletion:
        payload = synthesize(call.schema)
        text = json.dumps(payload, ensure_ascii=False, default=str)
        # Reported as unreported usage on purpose: a mock must not contribute fake token
        # counts to the ledger that a cost report would then treat as real.
        return RawCompletion(text=text, usage=TokenUsage(reported=False))


def synthesize(model: type[BaseModel], *, depth: int = 0) -> dict[str, Any]:
    """Build a minimal dict that validates against `model`.

    Required fields get a synthetic value; optional fields are left out so the result is
    the *smallest* valid instance — which is the interesting case for a schema test.
    """
    result: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if not field.is_required():
            continue
        result[name] = _value_for(field.annotation, name, field, depth=depth)
    return result


def _value_for(annotation: Any, name: str, field: FieldInfo, *, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return None

    origin = get_origin(annotation)

    if origin in (Union, types.UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if not args:
            return None
        return _value_for(args[0], name, field, depth=depth + 1)

    if origin is Literal:
        return get_args(annotation)[0]

    if origin in (list, set, frozenset, tuple):
        args = get_args(annotation)
        minimum = _min_length(field)
        if minimum <= 0 or not args:
            return []
        return [_value_for(args[0], name, field, depth=depth + 1) for _ in range(minimum)]

    if origin is dict:
        return {}

    if isinstance(annotation, type):
        if issubclass(annotation, Enum):
            return next(iter(annotation)).value
        if issubclass(annotation, BaseModel):
            return synthesize(annotation, depth=depth + 1)
        if issubclass(annotation, bool):
            return False
        if issubclass(annotation, int):
            return max(1, _min_value(field))
        if issubclass(annotation, float):
            return float(max(1, _min_value(field)))
        if issubclass(annotation, Decimal):
            return "1"
        if issubclass(annotation, datetime):
            return (datetime.now(UTC) + timedelta(days=7)).isoformat()
        if issubclass(annotation, date):
            return (datetime.now(UTC) + timedelta(days=7)).date().isoformat()
        if issubclass(annotation, UUID):
            return str(uuid5(MOCK_NAMESPACE, name))
        if issubclass(annotation, str):
            return _mock_string(name, field)

    return _mock_string(name, field)


def _mock_string(name: str, field: FieldInfo) -> str:
    value = f"mock-{name.replace('_', '-')}"
    minimum, maximum = _min_length(field), _max_length(field)
    if maximum is not None and len(value) > maximum:
        value = value[:maximum]
    if len(value) < minimum:
        value = value.ljust(minimum, "x")
    return value


def _constraint(field: FieldInfo, attr: str) -> Any:
    for meta in field.metadata:
        found = getattr(meta, attr, None)
        if found is not None:
            return found
    return None


def _min_length(field: FieldInfo) -> int:
    return int(_constraint(field, "min_length") or 0)


def _max_length(field: FieldInfo) -> int | None:
    value = _constraint(field, "max_length")
    return int(value) if value is not None else None


def _min_value(field: FieldInfo) -> int:
    for attr in ("ge", "gt"):
        value = _constraint(field, attr)
        if value is not None:
            return int(value) + (1 if attr == "gt" else 0)
    return 1

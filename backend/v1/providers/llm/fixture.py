"""Fixture-replay provider — deterministic, free, offline.

This is a first-class requirement rather than a test nicety. Both surviving SERP providers
charge per query and neither has a sustainable free monthly tier, and the same logic
applies to LLM spend: development must not burn quota, and CI must produce the same result
on every run. So `ci_fixture` is what CI and demos use, and live providers are opt-in.

**The nonce problem.** The port wraps untrusted content in a random per-call nonce, so the
prompt text differs on every run by construction. Hashing it verbatim would make every
lookup a miss. The key therefore normalises nonces out before hashing — the *intent* of the
call is stable even though its literal bytes are not.

**Recording.** Set `record_from: <live profile>` on a fixture profile and a missing fixture
is fetched from that live provider once and written to disk. Every later run replays it. A
missing fixture with no recording source raises `FixtureMissing` carrying the exact key,
path and skeleton needed to create it by hand.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from v1 import resolve_path
from v1.contracts.errors import FixtureMissing
from v1.contracts.llm import RawCompletion, TokenUsage
from v1.platform.logging import get_logger
from v1.providers.llm.base import BaseLLMProvider, WireCall

logger = get_logger("v1.llm.fixture")

DEFAULT_FIXTURE_DIR = "tests/v1/fixtures/llm"

_NONCE_RE = re.compile(r"untrusted_content_[0-9a-f]{6,32}", re.IGNORECASE)
_NONCE_PLACEHOLDER = "untrusted_content_NONCE"


def normalise_for_key(text: str) -> str:
    """Remove the per-call nonce so a recording keyed on intent stays findable."""
    return _NONCE_RE.sub(_NONCE_PLACEHOLDER, text)


def fixture_key(call: WireCall) -> str:
    """Stable key for one logical request: schema + normalised system + the first user turn.

    Two exclusions, both deliberate:

    * **Model, profile and adapter.** A response recorded from Gemini is a valid recording of
      *that request*; re-recording per provider would multiply the fixture set for no signal.
    * **The repair turns.** A retry carries the failed output and the validation error, so
      keying on the whole conversation would give attempt 2 a different key and a guaranteed
      miss. Keying on the original request means one recording covers the whole exchange —
      which is what makes a `responses: [invalid, valid]` fixture able to drive the repair
      path deterministically.
    """
    payload = json.dumps(
        {
            "schema": call.schema.__name__,
            "system": normalise_for_key(call.system),
            "user": normalise_for_key(call.turns[0].content) if call.turns else "",
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class FixtureProvider(BaseLLMProvider):
    adapter: ClassVar[str] = "fixture"

    def __init__(self, *, record_from: BaseLLMProvider | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._dir = resolve_path(self.profile.fixture_dir or DEFAULT_FIXTURE_DIR)
        self._record_from = record_from
        self._served: dict[str, int] = {}

    @property
    def fixture_dir(self) -> Path:
        return self._dir

    def set_record_source(self, provider: BaseLLMProvider | None) -> None:
        """Wired by the registry, and only when live spend is permitted for this process.

        Recording is the one path where a *costless* profile causes a real, paid call. The
        registry refuses to wire a paid source unless `V1_LLM_ALLOW_LIVE=1`, so
        `record_from` in a config file cannot on its own turn a fixture run into a bill.
        """
        self._record_from = provider

    def path_for(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    async def _invoke(self, call: WireCall) -> RawCompletion:
        key = fixture_key(call)
        path = self.path_for(key)

        if not path.is_file():
            if self._record_from is not None:
                return await self._record_fixture(call, key, path)
            raise FixtureMissing(
                f"no LLM fixture for key {key} (schema {call.schema.__name__}). "
                f"Write {path}, or set `record_from: <live profile>` on profile "
                f"{self.name!r} and run once against a live provider.",
                key=key,
                path=str(path),
                schema=call.schema.__name__,
            )

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FixtureMissing(
                f"fixture {path} could not be read: {exc}", key=key, path=str(path)
            ) from exc

        return self._serve(key, data)

    def _serve(self, key: str, data: dict[str, Any]) -> RawCompletion:
        """Return the recorded body.

        `responses` (a list) is served in order across calls with the same key, which is how
        the repair path is tested deterministically: element 0 is deliberately invalid,
        element 1 is valid.
        """
        usage = data.get("usage") or {}
        token_usage = TokenUsage(
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            reported=bool(usage) and bool(usage.get("reported", True)),
        )

        if isinstance(data.get("responses"), list) and data["responses"]:
            index = self._served.get(key, 0)
            self._served[key] = index + 1
            entry = data["responses"][min(index, len(data["responses"]) - 1)]
            return RawCompletion(text=_as_text(entry), usage=token_usage)

        if "text" in data:
            return RawCompletion(text=str(data["text"]), usage=token_usage)
        if "response" in data:
            return RawCompletion(text=_as_text(data["response"]), usage=token_usage)

        raise FixtureMissing(
            f"fixture for key {key} has none of `response`, `text` or `responses`",
            key=key,
            path=str(self.path_for(key)),
        )

    async def _record_fixture(self, call: WireCall, key: str, path: Path) -> RawCompletion:
        """Fetch a missing fixture from the live source once and persist it.

        Named `_record_fixture`, not `_record`: the base class already owns `_record` for the
        spend ledger, and shadowing it here would silently stop every fixture call from being
        accounted for.
        """
        assert self._record_from is not None
        source = self._record_from
        logger.warning(
            "llm_fixture_recording",
            key=key,
            path=str(path),
            source_profile=source.name,
            schema=call.schema.__name__,
        )
        # Accounted path, not `source._invoke`: recording is a real, paid call, and reaching
        # the adapter directly would skip the source's budget check and write no ledger row —
        # spend that the daily cap can never see.
        raw = await source.invoke_for_recording(call, task="fixture_record")
        if path.is_file():
            # A repair attempt shares the original request's key. Never overwrite the first
            # recording with a retry's output — that would rewrite history mid-run.
            return raw
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "_key": key,
                    "_schema": call.schema.__name__,
                    "_recorded_from": f"{source.adapter}:{source.model}",
                    "_recorded_at": datetime.now(UTC).isoformat(),
                    "_prompt_excerpt": normalise_for_key(call.turns[-1].content)[:2000],
                    "usage": {
                        "input_tokens": raw.usage.input_tokens,
                        "output_tokens": raw.usage.output_tokens,
                        "reported": raw.usage.reported,
                    },
                    "text": raw.text,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return raw


def _as_text(entry: Any) -> str:
    """A fixture may store the object itself or the raw string the provider returned."""
    if isinstance(entry, str):
        return entry
    return json.dumps(entry, ensure_ascii=False)

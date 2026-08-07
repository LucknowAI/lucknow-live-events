"""The LLM port.

Interface only. This module imports `contracts` and nothing else — no vendor SDKs, no
`platform`, no adapters. A module that depends on this file cannot tell which provider
is behind it, which is the whole point (ADR-014).

The port is deliberately one method wide. Everything the engine asks an LLM for is the
same shape of request: *here is an instruction, here is some (possibly hostile) text,
give me back an instance of this Pydantic model.* Streaming, chat history, tool loops and
agent runtimes are not in the contract because no phase of this engine needs them; adding
them later is a new method, not a rewrite.
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from v1.contracts.llm import LLMCapabilities, LLMResult

T = TypeVar("T", bound=BaseModel)


@runtime_checkable
class LLMProvider(Protocol):
    """One configured way to get a validated structured object out of a model."""

    name: str
    """The config profile name, e.g. `gemini_flash_lite`. Used in logs and accounting."""

    adapter: str
    """Which adapter implementation this is, e.g. `gemini_native`."""

    model: str
    """The model identifier as the provider knows it, or a sentinel for costless adapters."""

    capabilities: LLMCapabilities
    """What this profile can actually do — validated against config at startup."""

    async def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        task: str,
        untrusted_content: str | None = None,
    ) -> LLMResult[T]:
        """Return a validated instance of `schema`.

        Args:
            system: Trusted instruction. Never contains scraped text.
            user: Trusted instruction/framing for this specific call.
            schema: Pydantic model the response must validate against. Must not set
                `extra="forbid"` — see `v1.contracts.event` for why.
            task: Accounting and routing label (`extraction`, `classification`, ...).
            untrusted_content: Third-party text (scraped HTML, feed body). The
                implementation is responsible for isolating it from the instructions; a
                caller must never inline it into `user` itself.

        Raises:
            v1.contracts.errors.SchemaValidationFailed: output never validated, including
                after the single repair attempt. Never returns a partial object instead.
            v1.contracts.errors.ProviderError: transport, auth, timeout or refusal.
            v1.contracts.errors.BudgetExceeded: the spend cap blocked the call.
        """
        ...

    async def aclose(self) -> None:
        """Release transport resources. Safe to call more than once."""
        ...

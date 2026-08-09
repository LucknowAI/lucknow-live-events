"""Verdict types — a decision plus the reason it was made.

Every verdict in this engine carries a `reason` and the id of the rule that produced it.
That is not decoration: V0's discovery is undebuggable precisely because a URL was
dropped by one branch of a large regex and nobody can say which. A verdict you cannot
explain is a verdict you cannot tune.

Later phases add `ValidationVerdict`, `MatchVerdict` and `DeltaVerdict` here. Phase 2
needs the URL one.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class UrlVerdict(StrEnum):
    """What a discovered URL appears to be."""

    EVENT_PAGE = "event_page"
    """A single event's own page. The thing we are looking for."""

    LISTING = "listing"
    """A real page on a real event platform, but a directory rather than one event.
    Dropped as a candidate, but *recorded* — a listing page on a domain we do not know
    yet is how a new source gets found."""

    IRRELEVANT = "irrelevant"
    """Not an event page and not worth remembering — a blocked host, a social profile, a
    file download. Distinguished from LISTING on purpose: the parent plan's three-verdict
    scheme lumped these together, and only one of the two is a source-discovery signal."""

    UNKNOWN = "unknown"
    """No rule matched. Batched into one LLM triage call per run, never one per URL."""


class DecidedBy(StrEnum):
    RULE = "rule"
    LLM = "llm"


class UrlClassification(BaseModel):
    """One URL's verdict, with the reason and the rule that produced it."""

    model_config = ConfigDict(frozen=True)

    url: str = Field(min_length=1, max_length=2048)
    verdict: UrlVerdict
    reason: str = Field(min_length=1, max_length=500)
    rule_id: str = Field(min_length=1, max_length=64)
    decided_by: DecidedBy = DecidedBy.RULE
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    """1.0 for deterministic rules. The LLM triage call reports its own."""

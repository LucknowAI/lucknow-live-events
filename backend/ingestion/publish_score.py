from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PublishInputs:
    source_trust_score: float
    extraction_confidence: float
    location_confidence: float  # relevance_score
    field_completeness: float   # 0–1 fraction of required fields present
    relevance_score: float
    dedup_certainty: float      # 1.0 = unique, 0.5 = uncertain


# Weights per requirements §8 publish decision formula.
_W_SOURCE = 0.25
_W_EXTRACTION = 0.20
_W_LOCATION = 0.20
_W_COMPLETENESS = 0.15
_W_RELEVANCE = 0.15
_W_DEDUP = 0.05


def compute_publish_score(inp: PublishInputs) -> float:
    return (
        inp.source_trust_score * _W_SOURCE
        + inp.extraction_confidence * _W_EXTRACTION
        + inp.location_confidence * _W_LOCATION
        + inp.field_completeness * _W_COMPLETENESS
        + inp.relevance_score * _W_RELEVANCE
        + inp.dedup_certainty * _W_DEDUP
    )


def publish_threshold(source_trust_score: float) -> float:
    """
    Dynamic publish threshold based on how trusted the source is.
    High-trust sources (e.g. GDG, Commudle) get a lower threshold so
    events with all core fields but missing venue still get published.
    Low-trust sources (e.g. user submissions) keep the strict 0.75 bar.
    """
    if source_trust_score >= 0.85:
        return 0.60
    if source_trust_score >= 0.70:
        return 0.68
    return 0.75


def field_completeness(data: dict) -> float:
    """Compute fraction of the key required fields that are non-null/non-empty."""
    required = ["title", "start_at", "canonical_url"]
    good = sum(1 for f in required if data.get(f))
    bonus_fields = ["short_description", "mode", "community_name", "organizer_name"]
    bonus = sum(0.1 for f in bonus_fields if data.get(f))
    base = good / len(required)
    return min(1.0, base + bonus)


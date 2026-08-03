"""Deterministic field resolver (Phase 2.1). Pure, no LLM.

Given the ACTIVE candidates for one event field, pick a resolved value by field-specific authority,
raise confidence on independent agreement, flag equal-authority disagreement as CONFLICT, never let
a stale/lower-authority candidate override a newer higher-authority one, and leave a field UNRESOLVED
rather than guess.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from crawl_service.enrichment.registry import (
    CANONICAL_ENTITY_RELATIONSHIP,
    FIELD_REGISTRY,
    TEMPORAL_OBSERVATION,
    Candidate,
)

# reason codes
RESOLVED_DIRECT = "RESOLVED_DIRECT"
RESOLVED_CONSENSUS = "RESOLVED_CONSENSUS"
RESOLVED_RELATIONSHIP = "RESOLVED_RELATIONSHIP"
RESOLVED_TEMPORAL_INTERVAL = "RESOLVED_TEMPORAL_INTERVAL"
CONFLICT = "CONFLICT"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

# resolution_method
DIRECT_SOURCE = "DIRECT_SOURCE"
STRUCTURED_SOURCE_CONSENSUS = "STRUCTURED_SOURCE_CONSENSUS"
CANONICAL_RELATIONSHIP = "CANONICAL_RELATIONSHIP"
TEMPORAL_INTERVAL = "TEMPORAL_INTERVAL"
UNRESOLVED = "UNRESOLVED"

# review_status
AUTO_RESOLVED = "AUTO_RESOLVED"
REVIEW_CONFLICT = "CONFLICT"
NEEDS_REVIEW = "NEEDS_REVIEW"
REVIEW_UNRESOLVED = "UNRESOLVED"


@dataclass
class Resolution:
    field_name: str
    resolved_value: Any
    resolution_method: str
    confidence: float
    review_status: str
    reason_code: str
    supporting: list[Candidate] = field(default_factory=list)
    contradicting: list[Candidate] = field(default_factory=list)


def _aware(dt: datetime | None) -> float:
    return dt.timestamp() if dt else 0.0


def resolve_field(field_name: str, candidates: list[Candidate], *, min_confidence: float | None = None) -> Resolution:
    spec = FIELD_REGISTRY.get(field_name)
    usable = [c for c in candidates if c.normalized_value is not None and spec and c.source_type in spec.precedence]
    if spec is None or not usable:
        return Resolution(field_name, None, UNRESOLVED, 0.0, REVIEW_UNRESOLVED, INSUFFICIENT_EVIDENCE)

    min_conf = spec.min_auto_confidence if min_confidence is None else min_confidence

    # Best authority present (lowest index). Prefer newer observed_at within the same authority.
    def sort_key(c: Candidate):
        return (spec.authority(c.source_type), -_aware(c.observed_at), -c.confidence)

    usable.sort(key=sort_key)
    top = usable[0]
    top_authority = spec.authority(top.source_type)
    top_tier = [c for c in usable if spec.authority(c.source_type) == top_authority]

    # Equal-authority disagreement -> CONFLICT (never silently pick one).
    top_values = {str(c.normalized_value) for c in top_tier}
    if len(top_values) > 1:
        return Resolution(
            field_name, None, UNRESOLVED, 0.0, REVIEW_CONFLICT, CONFLICT,
            supporting=[c for c in top_tier if str(c.normalized_value) == str(top.normalized_value)],
            contradicting=[c for c in top_tier if str(c.normalized_value) != str(top.normalized_value)],
        )

    value = top.normalized_value
    supporting = [c for c in usable if str(c.normalized_value) == str(value)]
    contradicting = [c for c in usable if str(c.normalized_value) != str(value)]

    # Independent agreement across distinct source types raises confidence.
    agreeing_types = {c.source_type for c in supporting}
    confidence = max(c.confidence for c in supporting)
    consensus = len(agreeing_types) > 1
    if consensus:
        confidence = min(0.99, confidence + 0.1)

    if top.source_type == CANONICAL_ENTITY_RELATIONSHIP:
        method, reason = CANONICAL_RELATIONSHIP, RESOLVED_RELATIONSHIP
    elif top.source_type == TEMPORAL_OBSERVATION:
        method, reason = TEMPORAL_INTERVAL, RESOLVED_TEMPORAL_INTERVAL
    elif consensus:
        method, reason = STRUCTURED_SOURCE_CONSENSUS, RESOLVED_CONSENSUS
    else:
        method, reason = DIRECT_SOURCE, RESOLVED_DIRECT

    review = AUTO_RESOLVED if confidence >= min_conf else NEEDS_REVIEW
    return Resolution(field_name, value, method, round(confidence, 3), review, reason,
                      supporting=supporting, contradicting=contradicting)

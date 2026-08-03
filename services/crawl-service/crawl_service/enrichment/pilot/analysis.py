"""Incremental-value analysis (Phase 2.2). Pure, deterministic.

Classifies a public-page candidate for one field against the existing API / canonical-graph evidence
and the current resolution, so a run can quantify NEW-FIELD gain vs same-family duplication vs
conflict vs freshness reconfirmation. Same-family reconfirmation is NEVER independent consensus.
"""

from __future__ import annotations

from datetime import datetime

from crawl_service.enrichment.registry import Candidate

# classifications
INCREMENTAL = "INCREMENTAL"          # adds a field the API/graph lacked
DUPLICATE = "DUPLICATE"              # same value already present (same family)
CONFLICT = "CONFLICT"               # materially different value
FRESHNESS_GAIN = "FRESHNESS_GAIN"    # same value but a materially newer reconfirmation of a mutable field
LOW_CONFIDENCE = "LOW_CONFIDENCE"    # below min confidence -> ignored
NO_VALUE = "NO_VALUE"               # no usable value

# suppression / reason codes surfaced in trace
NO_INCREMENTAL_VALUE = "NO_INCREMENTAL_VALUE"
SAME_SOURCE_FAMILY_DUPLICATE = "SAME_SOURCE_FAMILY_DUPLICATE"
STALE_PAGE_METADATA = "STALE_PAGE_METADATA"
CONFLICT_WITH_HIGHER_AUTHORITY = "CONFLICT_WITH_HIGHER_AUTHORITY"

_MUTABLE_FIELDS = frozenset({"starts_at", "venue_name", "venue_id", "city", "event_status"})
_DATE_FIELDS = frozenset({"starts_at", "end_at", "source_on_sale_at"})


def _as_dt(v):
    try:
        return datetime.fromisoformat(str(v)).replace(tzinfo=None)  # compare wall clock
    except (ValueError, TypeError):
        return None


def values_match(field: str, a, b) -> bool:
    if a is None or b is None:
        return a is b
    if field in _DATE_FIELDS:
        da, db = _as_dt(a), _as_dt(b)
        if da is not None and db is not None:
            return da == db
    return str(a) == str(b)


def classify_candidate(
    field: str,
    page_candidate: Candidate,
    *,
    api_value=None,
    graph_value=None,
    current_value=None,
    current_observed_at: datetime | None = None,
    min_confidence: float,
) -> dict:
    """Return {classification, reason, matched_authority}."""
    value = page_candidate.normalized_value
    if value is None:
        return {"classification": NO_VALUE, "reason": NO_VALUE}
    if page_candidate.confidence < min_confidence:
        return {"classification": LOW_CONFIDENCE, "reason": "LOW_CONFIDENCE"}

    existing = None
    matched = None
    for who, ev in (("api", api_value), ("graph", graph_value), ("current", current_value)):
        if ev is not None:
            existing, matched = ev, who
            break

    if existing is None:
        return {"classification": INCREMENTAL, "reason": "NEW_FIELD"}

    if values_match(field, value, existing):
        # same value; a materially newer reconfirmation of a mutable field is freshness (not consensus)
        if (field in _MUTABLE_FIELDS and current_observed_at is not None
                and page_candidate.observed_at is not None
                and page_candidate.observed_at > current_observed_at):
            return {"classification": FRESHNESS_GAIN, "reason": "NEWER_RECONFIRMATION",
                    "matched_authority": matched}
        return {"classification": DUPLICATE, "reason": SAME_SOURCE_FAMILY_DUPLICATE,
                "matched_authority": matched}

    return {"classification": CONFLICT, "reason": CONFLICT_WITH_HIGHER_AUTHORITY,
            "matched_authority": matched}

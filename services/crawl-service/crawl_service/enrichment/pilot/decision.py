"""Evidence-driven promotion recommendation (Phase 2.2). Pure, deterministic, configurable."""

from __future__ import annotations

from dataclasses import dataclass

PROMOTE_TO_STANDARD_ENRICHMENT = "PROMOTE_TO_STANDARD_ENRICHMENT"
KEEP_AS_FALLBACK = "KEEP_AS_FALLBACK"
DISABLE_LOW_VALUE = "DISABLE_LOW_VALUE"
REQUIRES_SOURCE_FIX = "REQUIRES_SOURCE_FIX"


@dataclass(frozen=True)
class DecisionThresholds:
    min_retrieval_success: float = 0.7
    min_incremental_gain_rate: float = 0.1
    max_conflict_rate: float = 0.2
    min_parser_success: float = 0.7


def _rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def recommend(metrics: dict, thresholds: DecisionThresholds) -> dict:
    """Compute a recommendation from measured metrics. Components + reasons are always returned."""
    attempted = metrics.get("pages_attempted", 0)
    valid = metrics.get("valid_event_pages", 0)
    fields_evaluated = metrics.get("fields_evaluated", 0)
    parser_failures = metrics.get("parser_failures", 0)

    retrieval_success = _rate(valid, attempted)
    incremental_gain_rate = _rate(metrics.get("incremental_field_gain_count", 0), fields_evaluated)
    conflict_rate = _rate(metrics.get("conflict_count", 0), fields_evaluated)
    parser_success = 1.0 - _rate(parser_failures, valid or attempted)
    freshness = metrics.get("freshness_gain_count", 0)

    components = {
        "retrieval_success": retrieval_success,
        "incremental_gain_rate": incremental_gain_rate,
        "conflict_rate": conflict_rate,
        "parser_success": round(parser_success, 4),
        "freshness_gain_count": freshness,
        "valid_event_pages": valid,
        "fields_evaluated": fields_evaluated,
    }
    reasons: list[str] = []

    if attempted == 0:
        return {"recommendation": DISABLE_LOW_VALUE,
                "components": components, "reasons": ["no pages attempted"]}

    if retrieval_success < thresholds.min_retrieval_success:
        reasons.append(f"retrieval_success {retrieval_success} < {thresholds.min_retrieval_success}")
    if parser_success < thresholds.min_parser_success:
        reasons.append(f"parser_success {round(parser_success,4)} < {thresholds.min_parser_success}")
    if reasons:
        return {"recommendation": REQUIRES_SOURCE_FIX, "components": components, "reasons": reasons}

    if (incremental_gain_rate >= thresholds.min_incremental_gain_rate
            and conflict_rate <= thresholds.max_conflict_rate):
        reasons.append(f"incremental gain {incremental_gain_rate} >= {thresholds.min_incremental_gain_rate}")
        return {"recommendation": PROMOTE_TO_STANDARD_ENRICHMENT, "components": components, "reasons": reasons}

    if incremental_gain_rate > 0 or freshness > 0:
        reasons.append("some incremental/freshness value but below promotion threshold")
        return {"recommendation": KEEP_AS_FALLBACK, "components": components, "reasons": reasons}

    reasons.append("no incremental field gain and no freshness value beyond API/graph (same-family duplicates)")
    return {"recommendation": DISABLE_LOW_VALUE, "components": components, "reasons": reasons}

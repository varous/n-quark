"""Source-value + venue-coverage reports (Phase 2.2). Aggregate over persisted runs / candidates.

These measure how well *known tracked events* are grounded — not what share of the whole market is
known (that is not a Phase 2.2 concern).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from crawl_service.models import EnrichmentCandidate, EnrichmentRun, EventFieldResolution


def _rate(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def source_value_report(session_factory, *, surface=None, field=None, start=None, end=None) -> dict:
    with session_factory() as s:
        stmt = select(EnrichmentRun)
        if start:
            stmt = stmt.where(EnrichmentRun.started_at >= _parse(start))
        if end:
            stmt = stmt.where(EnrichmentRun.started_at <= _parse(end))
        if surface:
            stmt = stmt.where(EnrichmentRun.surface == surface)
        runs = s.execute(stmt.order_by(EnrichmentRun.started_at)).scalars().all()

    agg = {k: 0 for k in (
        "pages_attempted", "pages_retrieved", "valid_event_pages", "candidates_created",
        "parser_failures", "challenge_or_block_count", "json_ld_present", "embedded_state_present",
        "open_graph_present", "fields_evaluated", "incremental_field_gain_count",
        "duplicate_evidence_count", "conflict_count", "freshness_gain_count", "bytes_received")}
    field_breakdown: dict[str, dict] = {}
    for r in runs:
        mt = r.metrics or {}
        for k in agg:
            agg[k] += int(mt.get(k, 0))
        for fname, counts in (mt.get("field_breakdown") or {}).items():
            if field and fname != field:
                continue
            fb = field_breakdown.setdefault(fname, {})
            for cls, n in counts.items():
                fb[cls] = fb.get(cls, 0) + int(n)

    valid = agg["valid_event_pages"]
    attempted = agg["pages_attempted"]
    fields_eval = agg["fields_evaluated"]
    return {
        "runs": len(runs), "surface": surface or "all",
        "pages_attempted": attempted, "valid_event_pages": valid,
        "retrieval_success_rate": _rate(valid, attempted),
        "challenge_or_block_rate": _rate(agg["challenge_or_block_count"], attempted),
        "parser_failure_rate": _rate(agg["parser_failures"], valid or attempted),
        "json_ld_presence_rate": _rate(agg["json_ld_present"], valid),
        "embedded_state_presence_rate": _rate(agg["embedded_state_present"], valid),
        "open_graph_presence_rate": _rate(agg["open_graph_present"], valid),
        "incremental_field_gain_count": agg["incremental_field_gain_count"],
        "incremental_field_gain_rate": _rate(agg["incremental_field_gain_count"], fields_eval),
        "duplicate_evidence_count": agg["duplicate_evidence_count"],
        "conflict_count": agg["conflict_count"],
        "conflict_rate": _rate(agg["conflict_count"], fields_eval),
        "freshness_gain_count": agg["freshness_gain_count"],
        "bytes_received": agg["bytes_received"],
        "field_breakdown": field_breakdown,
    }


def venue_coverage_report(session_factory) -> dict:
    """How well can known tracked events be grounded geographically?"""
    with session_factory() as s:
        events_with_venue_text = s.execute(
            select(func.count(func.distinct(EnrichmentCandidate.canonical_event_id)))
            .where(EnrichmentCandidate.field_name == "venue_name")
        ).scalar() or 0

        def _resolved(field, method=None):
            stmt = select(func.count(func.distinct(EventFieldResolution.canonical_event_id))).where(
                EventFieldResolution.field_name == field,
                EventFieldResolution.is_current.is_(True),
                EventFieldResolution.resolved_value.is_not(None))
            if method:
                stmt = stmt.where(EventFieldResolution.resolution_method == method)
            return s.execute(stmt).scalar() or 0

        with_venue_id = _resolved("venue_id")
        city_from_canonical = _resolved("city", "CANONICAL_RELATIONSHIP")
        region_from_canonical = _resolved("region_id", "CANONICAL_RELATIONSHIP")
        city_direct = _resolved("city", "DIRECT_SOURCE")

        # events that have a venue name candidate but no resolved venue_id -> unresolved venue
        resolved_ids = {r for (r,) in s.execute(
            select(EventFieldResolution.canonical_event_id).where(
                EventFieldResolution.field_name == "venue_id",
                EventFieldResolution.is_current.is_(True),
                EventFieldResolution.resolved_value.is_not(None))).all()}
        venue_name_rows = s.execute(
            select(EnrichmentCandidate.canonical_event_id, EnrichmentCandidate.normalized_value)
            .where(EnrichmentCandidate.field_name == "venue_name",
                   EnrichmentCandidate.candidate_status == "ACTIVE")).all()

    unresolved_names: dict[str, int] = {}
    unresolved_events = set()
    for eid, name in venue_name_rows:
        if eid not in resolved_ids and name:
            unresolved_events.add(eid)
            unresolved_names[name] = unresolved_names.get(name, 0) + 1
    top_unresolved = sorted(unresolved_names.items(), key=lambda kv: (-kv[1], kv[0]))[:10]

    return {
        "events_with_source_venue_text": events_with_venue_text,
        "events_with_canonical_venue_id": with_venue_id,
        "venue_resolution_rate": _rate(with_venue_id, events_with_venue_text),
        "events_with_city_from_canonical_venue": city_from_canonical,
        "events_with_region_from_canonical_venue": region_from_canonical,
        "events_using_direct_source_city": city_direct,
        "unresolved_venue_count": len(unresolved_events),
        "top_unresolved_venue_names": [{"venue_name": n, "events": c} for n, c in top_unresolved],
    }


def _parse(v):
    try:
        return datetime.fromisoformat(str(v))
    except (ValueError, TypeError):
        return None

"""Deterministic market read models (Phase 4A).

Pure aggregation over a normalized, already-loaded snapshot of observed events + entity metadata.
No network here (the datasource loads; this reshapes), no prediction, no scores, no total-market claim.
Every model counts **canonical** entities (folded through the `Canonicalizer`) so legacy naive-projection
nodes and their evidence-resolved canonicals are not double-counted.

Vocabulary: we report **observed supply** and **observation quality**, never market coverage.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime

from analytics_service.projection import Canonicalizer

# ---- transition-type classification (deterministic, matches Shadow Ledger vocabulary) ------------
PRICE_MARKERS = ("PRICE",)
AVAILABILITY_MARKERS = ("TICKETS_SOLD", "FILL_RATIO", "AVAILABILITY", "SOLD_OUT", "CAPACITY")
STATUS_MARKERS = ("STATUS", "CANCEL")
DATE_VENUE_MARKERS = ("DATE_CHANGED", "VENUE_CHANGED", "TIME_CHANGED", "RESCHEDULED")
DISAPPEAR_MARKERS = ("RECORD_ABSENT", "DISAPPEAR", "REAPPEAR", "RECORD_PRESENT_AGAIN")
CANCEL_MARKERS = ("CANCEL",)

STALE_GAP_HOURS = 48.0

SCOPE_NOTE = (
    "Observed supply only — counts reflect what n-quark has captured from the configured sources "
    "(Boshow, District), not total market activity. Entities are counted by canonical id."
)


# ---- normalized inputs --------------------------------------------------------------------------
@dataclass
class ObservedEvent:
    canonical_event_id: str
    source: str
    source_record_id: str | None = None
    city: str | None = None
    region: str | None = None
    category: str | None = None
    starts_at: str | None = None
    price_min: float | None = None
    price_max: float | None = None
    currency: str | None = None
    fill_ratio: float | None = None
    tickets_sold: int | None = None
    capacity: int | None = None
    # observation metrics (from the scheduler's capture coverage)
    capture_count: int = 0
    distinct_state_count: int = 0
    transition_count: int = 0
    capture_gap_hours: float | None = None
    last_capture_status: str | None = None
    consecutive_failures: int = 0
    consecutive_absences: int = 0
    out_of_order_count: int = 0
    # resolved canonical entities (canonical ids; may still contain legacy ids to be folded)
    artists: list[str] = field(default_factory=list)
    venues: list[str] = field(default_factory=list)
    organizers: list[str] = field(default_factory=list)
    series: list[str] = field(default_factory=list)
    has_unresolved_entities: bool = False
    # commercial-state: transition_type -> count
    transition_types: dict[str, int] = field(default_factory=dict)


@dataclass
class EntityMeta:
    canonical_entity_id: str
    entity_type: str
    canonical_name: str | None = None
    identity_state: str = "UNKNOWN"
    city: str | None = None
    region: str | None = None
    sources: list[str] = field(default_factory=list)
    # for series: whether it carries a strong recurrence marker (else weak/year-only → excluded)
    strong_series_marker: bool = False
    superseded: bool = False


@dataclass
class Dataset:
    events: list[ObservedEvent]
    entities: dict[str, EntityMeta]  # canonical_entity_id -> meta
    canonicalizer: Canonicalizer
    sources: list[str] = field(default_factory=list)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    warnings: list[str] = field(default_factory=list)


# ---- helpers ------------------------------------------------------------------------------------
def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)  # Python 3.11+ parses a trailing 'Z'
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _matches(markers: tuple[str, ...], ttype: str) -> bool:
    up = ttype.upper()
    return any(m in up for m in markers)


def _event_has(ev: ObservedEvent, markers: tuple[str, ...]) -> bool:
    return any(_matches(markers, t) for t in ev.transition_types)


def _count(markers: tuple[str, ...], ev: ObservedEvent) -> int:
    return sum(c for t, c in ev.transition_types.items() if _matches(markers, t))


@dataclass
class ScopedEvents:
    included: list[ObservedEvent]
    excluded: list[dict]  # {canonical_event_id, reason}


def scope_events(
    events: list[ObservedEvent],
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    source: str | None = None,
    city: str | None = None,
    region: str | None = None,
) -> ScopedEvents:
    """Filter events by the standard facets, recording *why* each excluded event was dropped."""
    df = _parse_dt(date_from) if date_from else None
    dt_to = _parse_dt(date_to) if date_to else None
    included: list[ObservedEvent] = []
    excluded: list[dict] = []
    for ev in events:
        if source and ev.source != source:
            excluded.append({"canonical_event_id": ev.canonical_event_id, "reason": f"source!={source}"})
            continue
        if city and (ev.city or "").lower() != city.lower():
            excluded.append({"canonical_event_id": ev.canonical_event_id, "reason": f"city!={city}"})
            continue
        if region and (ev.region or "").lower() != region.lower():
            excluded.append({"canonical_event_id": ev.canonical_event_id, "reason": f"region!={region}"})
            continue
        starts = _parse_dt(ev.starts_at)
        if df and (starts is None or starts < df):
            excluded.append({"canonical_event_id": ev.canonical_event_id, "reason": "before date_from"})
            continue
        if dt_to and (starts is None or starts > dt_to):
            excluded.append({"canonical_event_id": ev.canonical_event_id, "reason": "after date_to"})
            continue
        included.append(ev)
    return ScopedEvents(included=included, excluded=excluded)


def _temporal_counts(events: list[ObservedEvent], now: datetime) -> dict[str, int]:
    upcoming = completed = cancelled = undated = 0
    for ev in events:
        if _event_has(ev, CANCEL_MARKERS):
            cancelled += 1
            continue
        starts = _parse_dt(ev.starts_at)
        if starts is None:
            undated += 1
        elif starts > now:
            upcoming += 1
        else:
            completed += 1
    return {"upcoming": upcoming, "completed": completed, "cancelled": cancelled, "undated": undated}


def _source_distribution(events: list[ObservedEvent]) -> dict[str, int]:
    dist: dict[str, int] = {}
    for ev in events:
        dist[ev.source] = dist.get(ev.source, 0) + 1
    return dict(sorted(dist.items()))


def _unique_canonical(events: list[ObservedEvent], attr: str, ds: Dataset) -> list[str]:
    ids: list[str] = []
    for ev in events:
        ids.extend(getattr(ev, attr))
    return sorted(ds.canonicalizer.dedupe(ids))


def _name(ds: Dataset, cid: str) -> str | None:
    meta = ds.entities.get(cid)
    return meta.canonical_name if meta else None


def _first_last(events: list[ObservedEvent]) -> tuple[str | None, str | None]:
    dates = sorted(d for d in (ev.starts_at for ev in events) if d)
    return (dates[0], dates[-1]) if dates else (None, None)


def _longitudinal(ev: ObservedEvent) -> bool:
    return ev.transition_count >= 1 or ev.distinct_state_count >= 2 or ev.capture_count >= 2


# ---- regional read model ------------------------------------------------------------------------
def _region_key(ev: ObservedEvent) -> str | None:
    return ev.region or (f"city:{ev.city}" if ev.city else None)


def regional_supply(ds: Dataset, scoped: ScopedEvents) -> list[dict]:
    groups: dict[str, list[ObservedEvent]] = {}
    for ev in scoped.included:
        key = _region_key(ev)
        if key is None:
            continue  # counted separately under observation quality (missing geography)
        groups.setdefault(key, []).append(ev)
    rows = [_region_row(ds, key, evs) for key, evs in groups.items()]
    rows.sort(key=lambda r: (-r["observed_event_count"], r["region"]))
    return rows


def _region_row(ds: Dataset, key: str, events: list[ObservedEvent]) -> dict:
    temporal = _temporal_counts(events, ds.now)
    with_geo = sum(1 for ev in events if ev.city)
    cities = sorted({ev.city for ev in events if ev.city})
    return {
        "region": key,
        "region_name": ds.entities.get(key).canonical_name if ds.entities.get(key) else None,
        "cities": cities,
        "observed_event_count": len(events),
        "upcoming_event_count": temporal["upcoming"],
        "completed_event_count": temporal["completed"],
        "cancelled_event_count": temporal["cancelled"],
        "undated_event_count": temporal["undated"],
        "unique_canonical_artists": len(_unique_canonical(events, "artists", ds)),
        "unique_canonical_venues": len(_unique_canonical(events, "venues", ds)),
        "unique_canonical_organizers": len(_unique_canonical(events, "organizers", ds)),
        "source_distribution": _source_distribution(events),
        "events_with_resolved_geography": with_geo,
        "events_missing_geography": len(events) - with_geo,
    }


def region_detail(ds: Dataset, region_id: str, scoped: ScopedEvents) -> dict | None:
    events = [ev for ev in scoped.included if _region_key(ev) == region_id]
    if not events:
        return None
    row = _region_row(ds, region_id, events)
    by_city: dict[str, int] = {}
    for ev in events:
        c = ev.city or "—"
        by_city[c] = by_city.get(c, 0) + 1
    row["event_ids"] = sorted(ev.canonical_event_id for ev in events)
    row["by_city"] = dict(sorted(by_city.items()))
    row["artists"] = [{"canonical_entity_id": c, "name": _name(ds, c)}
                      for c in _unique_canonical(events, "artists", ds)]
    row["venues"] = [{"canonical_entity_id": c, "name": _name(ds, c)}
                     for c in _unique_canonical(events, "venues", ds)]
    return row


# ---- entity activity read models ----------------------------------------------------------------
def _events_featuring(ds: Dataset, attr: str, canonical_id: str, scoped: ScopedEvents) -> list[ObservedEvent]:
    out = []
    for ev in scoped.included:
        folded = {ds.canonicalizer.canonical_id(x) for x in getattr(ev, attr)}
        if canonical_id in folded:
            out.append(ev)
    return out


def _activity_common(ds: Dataset, events: list[ObservedEvent]) -> dict:
    temporal = _temporal_counts(events, ds.now)
    first, last = _first_last(events)
    return {
        "observed_event_count": len(events),
        "upcoming_event_count": temporal["upcoming"],
        "completed_event_count": temporal["completed"],
        "cancelled_event_count": temporal["cancelled"],
        "cities": sorted({ev.city for ev in events if ev.city}),
        "regions": sorted({ev.region for ev in events if ev.region}),
        "source_distribution": _source_distribution(events),
        "first_observed": first,
        "last_observed": last,
        "events_with_longitudinal_history": sum(1 for ev in events if _longitudinal(ev)),
        "event_ids": sorted(ev.canonical_event_id for ev in events),
    }


def artist_activity(ds: Dataset, canonical_id: str, scoped: ScopedEvents) -> dict | None:
    meta = ds.entities.get(canonical_id)
    events = _events_featuring(ds, "artists", canonical_id, scoped)
    if meta is None and not events:
        return None
    common = _activity_common(ds, events)
    return {
        "canonical_entity_id": canonical_id,
        "name": meta.canonical_name if meta else None,
        "identity_state": meta.identity_state if meta else "UNKNOWN",
        **common,
        "venues": [{"canonical_entity_id": c, "name": _name(ds, c)}
                   for c in _unique_canonical(events, "venues", ds)],
        "organizers": [{"canonical_entity_id": c, "name": _name(ds, c)}
                       for c in _unique_canonical(events, "organizers", ds)],
    }


def venue_activity(ds: Dataset, canonical_id: str, scoped: ScopedEvents) -> dict | None:
    meta = ds.entities.get(canonical_id)
    events = _events_featuring(ds, "venues", canonical_id, scoped)
    if meta is None and not events:
        return None
    common = _activity_common(ds, events)
    return {
        "canonical_entity_id": canonical_id,
        "name": meta.canonical_name if meta else None,
        "identity_state": meta.identity_state if meta else "UNKNOWN",
        "city": meta.city if meta else None,
        "region": meta.region if meta else None,
        "geography_provenance": "DIRECT_SOURCE_GEOGRAPHY_ONLY",
        **common,
        "categories": sorted({ev.category for ev in events if ev.category}),
        "artists": [{"canonical_entity_id": c, "name": _name(ds, c)}
                    for c in _unique_canonical(events, "artists", ds)],
        "organizers": [{"canonical_entity_id": c, "name": _name(ds, c)}
                       for c in _unique_canonical(events, "organizers", ds)],
        "events_with_state_transitions": sum(1 for ev in events if ev.transition_count >= 1),
    }


def organizer_activity(ds: Dataset, canonical_id: str, scoped: ScopedEvents) -> dict | None:
    meta = ds.entities.get(canonical_id)
    events = _events_featuring(ds, "organizers", canonical_id, scoped)
    if meta is None and not events:
        return None
    common = _activity_common(ds, events)
    venues = _unique_canonical(events, "venues", ds)
    series = _unique_canonical(events, "series", ds)
    return {
        "canonical_entity_id": canonical_id,
        "name": meta.canonical_name if meta else None,
        "identity_state": meta.identity_state if meta else "UNKNOWN",
        **common,
        "artists": [{"canonical_entity_id": c, "name": _name(ds, c)}
                    for c in _unique_canonical(events, "artists", ds)],
        "venues": [{"canonical_entity_id": c, "name": _name(ds, c)} for c in venues],
        "event_series": [{"canonical_entity_id": c, "name": _name(ds, c)} for c in series],
        "source_usage": _source_distribution(events),
        # deterministic recurrence indicators (not a score): operates across >1 venue / has a series
        "recurrence_indicators": {
            "distinct_venues": len(venues),
            "multi_venue": len(venues) > 1,
            "distinct_cities": len(common["cities"]),
            "multi_city": len(common["cities"]) > 1,
            "has_event_series": len(series) > 0,
        },
    }


def series_activity(ds: Dataset, canonical_id: str, scoped: ScopedEvents) -> dict | None:
    meta = ds.entities.get(canonical_id)
    if meta is None or meta.entity_type != "EVENT_SERIES":
        return None
    if not meta.strong_series_marker or meta.superseded:
        return None  # exclude weak / year-only / superseded series
    events = _events_featuring(ds, "series", canonical_id, scoped)
    first, last = _first_last(events)
    organizers = _unique_canonical(events, "organizers", ds)
    return {
        "canonical_entity_id": canonical_id,
        "name": meta.canonical_name,
        "identity_state": meta.identity_state,
        "edition_count": len(events),
        "linked_event_ids": sorted(ev.canonical_event_id for ev in events),
        "organizer": ({"canonical_entity_id": organizers[0], "name": _name(ds, organizers[0])}
                      if organizers else None),
        "cities": sorted({ev.city for ev in events if ev.city}),
        "venues": [{"canonical_entity_id": c, "name": _name(ds, c)}
                   for c in _unique_canonical(events, "venues", ds)],
        "source_distribution": _source_distribution(events),
        "first_edition_observed": first,
        "last_edition_observed": last,
    }


# ---- list builders (over entities of a given type) ----------------------------------------------
def _entities_of_type(ds: Dataset, etype: str) -> list[EntityMeta]:
    return [m for m in ds.entities.values() if m.entity_type == etype and not m.superseded]


def list_activity(ds: Dataset, etype: str, builder, scoped: ScopedEvents) -> list[dict]:
    rows = []
    for meta in _entities_of_type(ds, etype):
        row = builder(ds, meta.canonical_entity_id, scoped)
        if row and row["observed_event_count"] > 0:
            rows.append(row)
    # stable sort: most events desc, then canonical id asc
    rows.sort(key=lambda r: (-r["observed_event_count"], r["canonical_entity_id"]))
    return rows


def list_series(ds: Dataset, scoped: ScopedEvents) -> list[dict]:
    rows = []
    for meta in _entities_of_type(ds, "EVENT_SERIES"):
        row = series_activity(ds, meta.canonical_entity_id, scoped)
        if row:
            rows.append(row)
    rows.sort(key=lambda r: (-r["edition_count"], r["canonical_entity_id"]))
    return rows


# ---- observation-quality read model -------------------------------------------------------------
def observation_quality(ds: Dataset, scoped: ScopedEvents, *, by: str | None = None) -> dict:
    events = scoped.included

    def _metrics(evs: list[ObservedEvent]) -> dict:
        return {
            "tracked_events": len(evs),
            "events_captured_successfully": sum(
                1 for e in evs if (e.last_capture_status or "").startswith("SUCCESS") or e.capture_count > 0),
            "events_with_2plus_observations": sum(1 for e in evs if e.capture_count >= 2),
            "events_with_3plus_observations": sum(1 for e in evs if e.capture_count >= 3),
            "events_with_2plus_distinct_states": sum(1 for e in evs if e.distinct_state_count >= 2),
            "events_with_1plus_transitions": sum(1 for e in evs if e.transition_count >= 1),
            "average_capture_gap_hours": _avg([e.capture_gap_hours for e in evs]),
            "stale_events": sum(1 for e in evs if (e.capture_gap_hours or 0) >= STALE_GAP_HOURS),
            "partial_captures": sum(1 for e in evs if (e.last_capture_status or "") == "SUCCESS_PARTIAL"
                                    or "PARTIAL" in (e.last_capture_status or "")),
            "failed_captures": sum(1 for e in evs if (e.last_capture_status or "").startswith("FAIL")
                                   or e.consecutive_failures > 0),
            "out_of_order_captures": sum(1 for e in evs if e.out_of_order_count > 0),
            "events_with_unresolved_entities": sum(1 for e in evs if e.has_unresolved_entities),
            "events_missing_date": sum(1 for e in evs if not e.starts_at),
            "events_missing_venue": sum(1 for e in evs if not e.venues),
            "events_missing_geography": sum(1 for e in evs if not e.city),
        }

    result: dict = {"overall": _metrics(events)}
    if by == "source":
        buckets: dict[str, list[ObservedEvent]] = {}
        for e in events:
            buckets.setdefault(e.source, []).append(e)
        result["by_source"] = {k: _metrics(v) for k, v in sorted(buckets.items())}
    elif by == "region":
        buckets = {}
        for e in events:
            buckets.setdefault(_region_key(e) or "—", []).append(e)
        result["by_region"] = {k: _metrics(v) for k, v in sorted(buckets.items())}
    return result


def _avg(values: list[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 2) if present else None


# ---- commercial-state read model ----------------------------------------------------------------
def commercial_state(ds: Dataset, scoped: ScopedEvents) -> dict:
    events = scoped.included
    prices = [e.price_min for e in events if e.price_min is not None]
    # time from first observation to event start (hours), where both known
    lead_hours: list[float] = []
    for e in events:
        starts = _parse_dt(e.starts_at)
        # first observation ≈ event start minus capture history isn't stored here; use capture_gap as proxy
        if starts is not None and e.capture_gap_hours is not None:
            lead = (starts - ds.now).total_seconds() / 3600.0
            if lead > 0:
                lead_hours.append(round(lead, 1))
    price_by_source: dict[str, dict] = {}
    for e in events:
        if e.price_min is None:
            continue
        b = price_by_source.setdefault(e.source, {"currency": e.currency, "values": []})
        b["values"].append(e.price_min)
    price_source_summary = {
        src: {"currency": b["currency"], "count": len(b["values"]),
              "min": min(b["values"]), "max": max(b["values"]),
              "median": round(statistics.median(b["values"]), 2)}
        for src, b in sorted(price_by_source.items())
    }
    return {
        "events_with_price_observations": sum(1 for e in events if e.price_min is not None),
        "events_with_price_changes": sum(1 for e in events if _event_has(e, PRICE_MARKERS)),
        "events_with_availability_changes": sum(1 for e in events if _event_has(e, AVAILABILITY_MARKERS)),
        "events_with_status_changes": sum(1 for e in events if _event_has(e, STATUS_MARKERS)),
        "events_with_date_or_venue_changes": sum(1 for e in events if _event_has(e, DATE_VENUE_MARKERS)),
        "events_disappeared_or_reappeared": sum(1 for e in events if _event_has(e, DISAPPEAR_MARKERS)
                                                or e.consecutive_absences > 0),
        "observed_price_changes_total": sum(_count(PRICE_MARKERS, e) for e in events),
        "observed_availability_changes_total": sum(_count(AVAILABILITY_MARKERS, e) for e in events),
        "displayed_price": (
            {"count": len(prices), "min": min(prices), "max": max(prices),
             "median": round(statistics.median(prices), 2)} if prices else None),
        "price_by_source": price_source_summary,  # source-specific prices kept separate
        "time_to_event_hours": (
            {"count": len(lead_hours), "min": min(lead_hours), "max": max(lead_hours),
             "median": round(statistics.median(lead_hours), 1)} if lead_hours else None),
    }


# ---- trace --------------------------------------------------------------------------------------
def build_trace(ds: Dataset, scoped: ScopedEvents, *, metric_definitions: dict[str, str]) -> dict:
    folds = [
        {"input_entity_id": c.input_entity_id, "canonical_entity_id": c.canonical_entity_id,
         "resolution_path": c.resolution_path, "identity_state": c.identity_state,
         "warnings": c.warnings}
        for c in ds.canonicalizer.superseded_folds if c.folded
    ]
    warnings = list(ds.warnings)
    for c in ds.canonicalizer.superseded_folds:
        warnings.extend(c.warnings)
    return {
        "source_events_included": sorted(e.canonical_event_id for e in scoped.included),
        "source_events_excluded": scoped.excluded,
        "canonical_resolution_paths": folds,
        "superseded_identities_deduplicated": [f["input_entity_id"] for f in folds],
        "missing_field_exclusions": [
            {"canonical_event_id": e.canonical_event_id,
             "missing": [f for f in ("starts_at", "city", "venues")
                         if not getattr(e, f)]}
            for e in scoped.included
            if not e.starts_at or not e.city or not e.venues
        ],
        "metric_definitions": metric_definitions,
        "observation_quality_warnings": sorted(set(warnings)),
    }


def scope_block(ds: Dataset, extra: dict | None = None) -> dict:
    block = {
        "observation_scope": SCOPE_NOTE,
        "sources": ds.sources,
        "as_of": ds.now.isoformat(),
        "limitations": [
            "Observed supply only; not total-market coverage.",
            "Entities counted by canonical id (legacy projections folded, never double-counted).",
            "Unmerged legacy/canonical duplicates without a SUPERSEDED_BY edge are not silently merged.",
            "No prediction, demand, popularity or sell-through is computed.",
        ],
    }
    if extra:
        block.update(extra)
    return block

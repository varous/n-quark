"""Read-model aggregation — deterministic counts, dedup, filters, sort, trace, quality, commercial."""

from datetime import UTC, datetime

import pytest

from analytics_service import readmodels as rm
from analytics_service.projection import Canonicalizer

NOW = datetime(2026, 8, 5, tzinfo=UTC)


@pytest.fixture()
def ds() -> rm.Dataset:
    entities = {
        "artist:skinny-mos": rm.EntityMeta("artist:skinny-mos", "ARTIST", "Skinny Mos", "CANONICAL"),
        "venue:skinny-mos--kolkata": rm.EntityMeta("venue:skinny-mos--kolkata", "VENUE", "Skinny Mos", "CANONICAL", city="Kolkata"),
        "venue:grand-hall--kolkata": rm.EntityMeta("venue:grand-hall--kolkata", "VENUE", "Grand Hall", "CANONICAL", city="Kolkata"),
        "venue:legacy-hall": rm.EntityMeta("venue:legacy-hall", "VENUE", "Grand Hall (legacy)", "SUPERSEDED", superseded=True),
        "organizer:acme": rm.EntityMeta("organizer:acme", "ORGANIZER", "Acme Events", "CANONICAL"),
        "series:the-abomination": rm.EntityMeta("series:the-abomination", "EVENT_SERIES", "The Abomination", "CANONICAL", strong_series_marker=True),
        "series:weak-year": rm.EntityMeta("series:weak-year", "EVENT_SERIES", "Summer 2026", "CANONICAL", strong_series_marker=False),
    }
    canon = Canonicalizer(supersession={"venue:legacy-hall": "venue:grand-hall--kolkata"},
                          identity_states={k: v.identity_state for k, v in entities.items()},
                          known_ids=set(entities))
    events = [
        rm.ObservedEvent("event:e1", "boshow", city="Kolkata", region="region:west-bengal",
                         starts_at="2026-09-01T20:00:00+00:00", price_min=599.0, currency="INR",
                         capture_count=3, distinct_state_count=2, transition_count=2,
                         capture_gap_hours=2.0, last_capture_status="SUCCESS_RECORD_PRESENT",
                         artists=["artist:skinny-mos"], venues=["venue:skinny-mos--kolkata"],
                         organizers=["organizer:acme"], series=["series:the-abomination"],
                         transition_types={"PUBLIC_FILL_RATIO_CHANGED": 1, "EVENT_DATE_CHANGED": 1}),
        rm.ObservedEvent("event:e2", "district", city="Mumbai",
                         starts_at="2026-07-01T20:00:00+00:00", price_min=999.0, currency="INR",
                         capture_count=1, distinct_state_count=1, transition_count=0,
                         capture_gap_hours=100.0, last_capture_status="SUCCESS_RECORD_PRESENT",
                         artists=["artist:skinny-mos"], venues=["venue:legacy-hall"],
                         transition_types={"PUBLIC_PRICE_CHANGED": 1}),
        rm.ObservedEvent("event:e3", "boshow", city=None, starts_at=None,
                         capture_count=2, distinct_state_count=2, transition_count=0,
                         has_unresolved_entities=True, venues=[]),
    ]
    return rm.Dataset(events=events, entities=entities, canonicalizer=canon,
                      sources=["boshow", "district"], now=NOW)


def _scoped(ds, **f):
    return rm.scope_events(ds.events, **f)


# ---- regional --------------------------------------------------------------------------------
def test_regional_supply_groups_and_dedups(ds):
    rows = rm.regional_supply(ds, _scoped(ds))
    keys = {r["region"] for r in rows}
    assert keys == {"region:west-bengal", "city:Mumbai"}  # e3 (no geography) excluded from regions
    wb = next(r for r in rows if r["region"] == "region:west-bengal")
    assert wb["observed_event_count"] == 1 and wb["upcoming_event_count"] == 1
    assert wb["unique_canonical_venues"] == 1


def test_region_detail_city_breakdown(ds):
    row = rm.region_detail(ds, "region:west-bengal", _scoped(ds))
    assert row["event_ids"] == ["event:e1"]
    assert row["by_city"] == {"Kolkata": 1}


# ---- filters ---------------------------------------------------------------------------------
def test_source_filter_excludes_with_reason(ds):
    scoped = _scoped(ds, source="boshow")
    assert {e.canonical_event_id for e in scoped.included} == {"event:e1", "event:e3"}
    assert any(x["canonical_event_id"] == "event:e2" for x in scoped.excluded)


def test_date_filter(ds):
    scoped = _scoped(ds, date_from="2026-08-01T00:00:00+00:00")
    assert {e.canonical_event_id for e in scoped.included} == {"event:e1"}  # e2 past, e3 undated


def test_city_filter(ds):
    scoped = _scoped(ds, city="mumbai")
    assert {e.canonical_event_id for e in scoped.included} == {"event:e2"}


# ---- entity activity -------------------------------------------------------------------------
def test_artist_activity_folds_legacy_venue(ds):
    row = rm.artist_activity(ds, "artist:skinny-mos", _scoped(ds))
    assert row["observed_event_count"] == 2
    assert row["upcoming_event_count"] == 1 and row["completed_event_count"] == 1
    vids = {v["canonical_entity_id"] for v in row["venues"]}
    assert vids == {"venue:skinny-mos--kolkata", "venue:grand-hall--kolkata"}  # legacy folded
    assert row["events_with_longitudinal_history"] == 1


def test_venue_activity_reached_via_legacy(ds):
    row = rm.venue_activity(ds, "venue:grand-hall--kolkata", _scoped(ds))
    assert row["observed_event_count"] == 1 and row["event_ids"] == ["event:e2"]
    assert row["geography_provenance"] == "DIRECT_SOURCE_GEOGRAPHY_ONLY"


def test_organizer_recurrence_indicators(ds):
    row = rm.organizer_activity(ds, "organizer:acme", _scoped(ds))
    assert row["observed_event_count"] == 1
    assert row["recurrence_indicators"]["has_event_series"] is True


def test_list_activity_stable_sort(ds):
    rows = rm.list_activity(ds, "VENUE", rm.venue_activity, _scoped(ds))
    # superseded legacy venue excluded; two canonical venues each with 1 event, sorted by id
    ids = [r["canonical_entity_id"] for r in rows]
    assert ids == sorted(ids)
    assert "venue:legacy-hall" not in ids


# ---- series ----------------------------------------------------------------------------------
def test_series_strong_included_weak_excluded(ds):
    strong = rm.series_activity(ds, "series:the-abomination", _scoped(ds))
    assert strong["edition_count"] == 1 and strong["linked_event_ids"] == ["event:e1"]
    assert rm.series_activity(ds, "series:weak-year", _scoped(ds)) is None
    listed = {r["canonical_entity_id"] for r in rm.list_series(ds, _scoped(ds))}
    assert listed == {"series:the-abomination"}


# ---- observation quality ---------------------------------------------------------------------
def test_observation_quality_metrics(ds):
    q = rm.observation_quality(ds, _scoped(ds))["overall"]
    assert q["tracked_events"] == 3
    assert q["events_with_2plus_observations"] == 2 and q["events_with_3plus_observations"] == 1
    assert q["events_with_2plus_distinct_states"] == 2
    assert q["events_with_1plus_transitions"] == 1
    assert q["events_missing_date"] == 1 and q["events_missing_geography"] == 1
    assert q["events_missing_venue"] == 1 and q["events_with_unresolved_entities"] == 1


def test_observation_quality_by_source(ds):
    q = rm.observation_quality(ds, _scoped(ds), by="source")
    assert set(q["by_source"]) == {"boshow", "district"}
    assert q["by_source"]["district"]["tracked_events"] == 1


# ---- commercial state ------------------------------------------------------------------------
def test_commercial_state_summaries(ds):
    c = rm.commercial_state(ds, _scoped(ds))
    assert c["events_with_price_observations"] == 2
    assert c["events_with_price_changes"] == 1          # e2 PUBLIC_PRICE_CHANGED
    assert c["events_with_availability_changes"] == 1   # e1 FILL_RATIO
    assert c["events_with_date_or_venue_changes"] == 1  # e1 DATE
    assert c["displayed_price"] == {"count": 2, "min": 599.0, "max": 999.0, "median": 799.0}
    assert set(c["price_by_source"]) == {"boshow", "district"}  # kept separate
    assert c["price_by_source"]["district"]["min"] == 999.0


# ---- trace -----------------------------------------------------------------------------------
def test_trace_reports_folds_and_exclusions(ds):
    scoped = _scoped(ds, source="boshow")
    tr = rm.build_trace(ds, scoped, metric_definitions={"x": "y"})
    assert "event:e2" in [x["canonical_event_id"] for x in tr["source_events_excluded"]]
    assert tr["superseded_identities_deduplicated"] == ["venue:legacy-hall"]
    assert any(e["canonical_event_id"] == "event:e3" for e in tr["missing_field_exclusions"])

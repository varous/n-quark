from datetime import UTC, datetime, timedelta

from crawl_service.enrichment.pilot.analysis import (
    CONFLICT,
    DUPLICATE,
    FRESHNESS_GAIN,
    INCREMENTAL,
    LOW_CONFIDENCE,
    NO_VALUE,
    classify_candidate,
    values_match,
)
from crawl_service.enrichment.registry import OPEN_GRAPH, Candidate

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _c(field, value, conf=0.7, observed_at=NOW):
    return Candidate(field_name=field, candidate_value=value, source_type=OPEN_GRAPH,
                     extraction_method="PAGE_METADATA", confidence=conf, observed_at=observed_at).normalize()


def test_incremental_when_nothing_exists():
    r = classify_candidate("venue_name", _c("venue_name", "Skinny Mos"), min_confidence=0.5)
    assert r["classification"] == INCREMENTAL


def test_duplicate_when_matches_api():
    r = classify_candidate("venue_name", _c("venue_name", "Skinny Mos"),
                           api_value="Skinny Mos", min_confidence=0.5)
    assert r["classification"] == DUPLICATE


def test_date_wall_clock_duplicate_across_tz():
    # page naive 20:00 vs API 20:00+00:00 -> same information -> duplicate, not conflict
    assert values_match("starts_at", "2026-08-01T20:00:00", "2026-08-01T20:00:00+00:00")
    r = classify_candidate("starts_at", _c("starts_at", "2026-08-01T20:00:00", 0.6),
                           api_value="2026-08-01T20:00:00+00:00", min_confidence=0.5)
    assert r["classification"] == DUPLICATE


def test_conflict_when_differs():
    r = classify_candidate("starts_at", _c("starts_at", "2026-08-02T20:00:00", 0.6),
                           api_value="2026-08-01T20:00:00+00:00", min_confidence=0.5)
    assert r["classification"] == CONFLICT


def test_freshness_gain_for_newer_mutable_reconfirmation():
    r = classify_candidate("event_status", _c("event_status", "Live", 0.6, observed_at=NOW + timedelta(days=1)),
                           current_value="Live", current_observed_at=NOW, min_confidence=0.5)
    assert r["classification"] == FRESHNESS_GAIN


def test_low_confidence_ignored():
    r = classify_candidate("venue_name", _c("venue_name", "Skinny Mos", conf=0.3), min_confidence=0.6)
    assert r["classification"] == LOW_CONFIDENCE


def test_no_value():
    r = classify_candidate("venue_name", _c("venue_name", None), min_confidence=0.5)
    assert r["classification"] == NO_VALUE

from datetime import UTC, datetime, timedelta

from crawl_service.reconciliation.matcher import (
    AUTO_MATCH,
    CONFLICT,
    NOT_MATCHED,
    POSSIBLE_MATCH,
    in_block,
    score_match,
)
from crawl_service.reconciliation.views import EventView

D = datetime(2026, 9, 10, 19, 0, tzinfo=UTC)
TOL = 36
TH = {"date_tolerance_hours": TOL, "auto_threshold": 0.75, "possible_threshold": 0.5}


def _ev(source, sid, **kw):
    return EventView(source=source, source_record_id=sid, canonical_event_id=f"event:{sid}", **kw)


# ---- blocking -----------------------------------------------------------------------------------
def test_block_same_city_date_and_shared_token():
    a = _ev("boshow", "a", title="Prateek Kuhad Live", city="Mumbai", starts_at=D)
    b = _ev("district", "b", title="Prateek Kuhad", city="Mumbai", starts_at=D)
    assert in_block(a, b, date_tolerance_hours=TOL)[0] is True


def test_block_rejects_different_city():
    a = _ev("boshow", "a", title="Prateek Kuhad", city="Mumbai", starts_at=D)
    b = _ev("district", "b", title="Prateek Kuhad", city="Delhi", starts_at=D)
    assert in_block(a, b, date_tolerance_hours=TOL) == (False, "CITY_MISMATCH")


def test_block_rejects_date_outside_tolerance():
    a = _ev("boshow", "a", title="X Fest", city="Pune", starts_at=D)
    b = _ev("district", "b", title="X Fest", city="Pune", starts_at=D + timedelta(days=5))
    assert in_block(a, b, date_tolerance_hours=TOL) == (False, "DATE_OUTSIDE_TOLERANCE")


def test_block_requires_some_shared_signal():
    a = _ev("boshow", "a", title="Jazz Night", city="Pune", starts_at=D)
    b = _ev("district", "b", title="Comedy Gala", city="Pune", starts_at=D)
    assert in_block(a, b, date_tolerance_hours=TOL) == (False, "INSUFFICIENT_SIGNALS")


# ---- scoring ------------------------------------------------------------------------------------
def test_performer_date_venue_auto_match():
    a = _ev("boshow", "a", title="Prateek Kuhad Live", city="Mumbai", starts_at=D,
            venue_name="Phoenix Marketcity", performers=["Prateek Kuhad"])
    b = _ev("district", "b", title="Prateek Kuhad", city="Mumbai", starts_at=D,
            venue_name="Phoenix Marketcity", performers=["Prateek Kuhad"])
    r = score_match(a, b, **TH)
    assert r.status == AUTO_MATCH and "PERFORMER_OVERLAP" in r.supporting


def test_title_date_organizer_auto_match():
    a = _ev("boshow", "a", title="Mandala Art Workshop", city="Bengaluru", starts_at=D,
            organizer="ArtHouse")
    b = _ev("district", "b", title="Mandala Art Workshop", city="Bengaluru", starts_at=D,
            organizer="ArtHouse")
    r = score_match(a, b, **TH)
    assert r.status == AUTO_MATCH and "ORGANIZER_MATCH" in r.supporting


def test_title_only_does_not_auto_match():
    # same title, but no date and no other agreeing dimension -> not auto
    a = _ev("boshow", "a", title="Saturday Live")
    b = _ev("district", "b", title="Saturday Live")
    r = score_match(a, b, **TH)
    assert r.status != AUTO_MATCH


def test_strong_contradiction_blocks_match_despite_title():
    a = _ev("boshow", "a", title="Saturday Live", city="Mumbai", starts_at=D, venue_name="Venue A")
    b = _ev("district", "b", title="Saturday Live", city="Delhi",
            starts_at=D + timedelta(days=10), venue_name="Venue B")
    r = score_match(a, b, **TH)
    assert r.status in (CONFLICT, NOT_MATCHED) and "CITY_MISMATCH" in r.contradicting


def test_medium_score_is_possible_match():
    a = _ev("boshow", "a", title="Indie Folk Evening", city="Pune", starts_at=D)
    b = _ev("district", "b", title="Indie Folk Night", city="Pune", starts_at=D + timedelta(hours=2))
    r = score_match(a, b, **TH)
    assert r.status in (POSSIBLE_MATCH, AUTO_MATCH)  # title-ish + date + city agree


def test_component_scores_are_deterministic():
    a = _ev("boshow", "a", title="X", city="Pune", starts_at=D)
    b = _ev("district", "b", title="X", city="Pune", starts_at=D)
    assert score_match(a, b, **TH).components == score_match(a, b, **TH).components

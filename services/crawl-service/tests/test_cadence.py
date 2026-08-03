from datetime import UTC, datetime, timedelta

from crawl_service.cadence import (
    EVENT_DAY,
    FAR_FUTURE,
    FINAL,
    MID,
    NO_DATE,
    ONSALE_BURST,
    POST_EVENT,
    POST_EVENT_COMPLETE,
    TRACKING_STOPPED,
    compute_cadence,
)

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _hours(nxt: datetime) -> float:
    return round((nxt - NOW).total_seconds() / 3600.0, 2)


def test_far_future():
    nxt, reason = compute_cadence(NOW, starts_at=NOW + timedelta(days=60))
    assert reason == FAR_FUTURE and _hours(nxt) == 24


def test_mid_window():
    nxt, reason = compute_cadence(NOW, starts_at=NOW + timedelta(days=20))
    assert reason == MID and _hours(nxt) == 12


def test_final_14_days():
    nxt, reason = compute_cadence(NOW, starts_at=NOW + timedelta(days=5))
    assert reason == FINAL and _hours(nxt) == 4


def test_event_day():
    nxt, reason = compute_cadence(NOW, starts_at=NOW + timedelta(hours=6))
    assert reason == EVENT_DAY and _hours(nxt) == 2


def test_onsale_burst_takes_precedence_in_first_48h():
    # event is far away, but tickets just went on sale -> burst cadence
    nxt, reason = compute_cadence(
        NOW, starts_at=NOW + timedelta(days=40), on_sale_at=NOW - timedelta(hours=2)
    )
    assert reason == ONSALE_BURST and _hours(nxt) == 2


def test_post_event_followups_then_complete():
    starts = NOW - timedelta(hours=2)  # already started
    nxt, reason = compute_cadence(NOW, starts_at=starts)
    assert reason == POST_EVENT and nxt == starts + timedelta(days=1)
    # after +7d follow-up, tracking stops
    later = starts + timedelta(days=8)
    nxt2, reason2 = compute_cadence(later, starts_at=starts)
    assert nxt2 is None and reason2 == POST_EVENT_COMPLETE


def test_no_event_date_is_conservative():
    nxt, reason = compute_cadence(NOW, starts_at=None)
    assert reason == NO_DATE and _hours(nxt) == 24


def test_terminal_status_stops():
    nxt, reason = compute_cadence(NOW, starts_at=NOW + timedelta(days=5), tracking_status="CANCELLED")
    assert nxt is None and reason == TRACKING_STOPPED


def test_deterministic():
    a = compute_cadence(NOW, starts_at=NOW + timedelta(days=5))
    b = compute_cadence(NOW, starts_at=NOW + timedelta(days=5))
    assert a == b

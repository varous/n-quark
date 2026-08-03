from datetime import UTC, datetime, timedelta

from crawl_service.priority import compute_priority

NOW = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def test_event_day_outranks_far_future():
    near, _, _ = compute_priority(NOW, starts_at=NOW + timedelta(hours=6))
    far, _, _ = compute_priority(NOW, starts_at=NOW + timedelta(days=60))
    assert near > far


def test_onsale_burst_and_recent_transition_boost():
    base, _, _ = compute_priority(NOW, starts_at=NOW + timedelta(days=40))
    boosted, _reason, comps = compute_priority(
        NOW, starts_at=NOW + timedelta(days=40), on_sale_at=NOW - timedelta(hours=1),
        last_state_change_at=NOW - timedelta(days=1),
    )
    assert boosted > base
    assert "onsale_burst" in comps and "recent_transition" in comps


def test_city_allowlist_boost():
    _, _, comps = compute_priority(
        NOW, starts_at=NOW + timedelta(days=10), city="Kolkata",
        city_allowlist=frozenset({"kolkata"}),
    )
    assert comps.get("priority_city") == 10


def test_failure_penalty_and_clamp():
    score, _, comps = compute_priority(
        NOW, starts_at=NOW + timedelta(days=60), consecutive_failures=5
    )
    assert comps["failure_penalty"] == -15  # capped at 3 * -5
    assert 0 <= score <= 100


def test_reason_and_components_are_deterministic():
    a = compute_priority(NOW, starts_at=NOW + timedelta(days=5))
    b = compute_priority(NOW, starts_at=NOW + timedelta(days=5))
    assert a == b

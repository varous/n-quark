from datetime import UTC, datetime

from api_gateway.admin.event_lifecycle import derive_temporal_state, normalize_provider_lifecycle


NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


def test_datetime_states_transition_without_source_mutation():
    evidence = {"starts_at": "2026-08-14T10:00:00+00:00", "ends_at": "2026-08-14T14:00:00+00:00"}
    assert derive_temporal_state(**evidence, evaluated_at=datetime(2026, 8, 14, 9, tzinfo=UTC))["temporal_state"] == "UPCOMING"
    assert derive_temporal_state(**evidence, evaluated_at=NOW)["temporal_state"] == "ONGOING"
    assert derive_temporal_state(**evidence, evaluated_at=datetime(2026, 8, 14, 15, tzinfo=UTC))["temporal_state"] == "PAST"


def test_start_only_never_invents_duration_or_completion():
    result = derive_temporal_state(starts_at="2026-08-14T10:00:00+00:00", evaluated_at=NOW)
    assert result["temporal_state"] == "PAST"
    assert result["temporal_basis"] == "START_DATETIME_ONLY"
    assert "COMPLETED" not in result.values()


def test_date_only_uses_explicit_local_day_and_today_is_unknown():
    assert derive_temporal_state(event_date="2026-08-13", evaluated_at=NOW, local_timezone="Asia/Kolkata")["temporal_state"] == "PAST"
    assert derive_temporal_state(event_date="2026-08-15", evaluated_at=NOW, local_timezone="Asia/Kolkata")["temporal_state"] == "UPCOMING"
    assert derive_temporal_state(event_date="2026-08-14", evaluated_at=NOW, local_timezone="Asia/Kolkata")["temporal_state"] == "UNKNOWN"


def test_naive_datetime_and_timezone_unknown_remain_unknown():
    assert derive_temporal_state(starts_at="2026-08-15T19:00:00", evaluated_at=NOW)["temporal_state"] == "UNKNOWN"
    assert derive_temporal_state(event_date="2026-08-15", evaluated_at=NOW)["temporal_state"] == "UNKNOWN"


def test_provider_lifecycle_is_independent_and_schema_org_aware():
    assert normalize_provider_lifecycle("https://schema.org/EventCancelled") == "CANCELLED"
    assert normalize_provider_lifecycle("EventRescheduled") == "RESCHEDULED"
    assert normalize_provider_lifecycle(None) == "UNKNOWN"


def test_shared_contract_fixture_matches_crawl_contract():
    fixtures = [
        ({"starts_at": "2026-08-15T10:00:00+00:00"}, "UPCOMING"),
        ({"starts_at": "2026-08-14T10:00:00+00:00", "ends_at": "2026-08-14T14:00:00+00:00"}, "ONGOING"),
        ({"starts_at": "2026-08-13T10:00:00+00:00"}, "PAST"),
        ({"event_date": "2026-08-14", "local_timezone": "Asia/Kolkata"}, "UNKNOWN"),
    ]
    for kwargs, expected in fixtures:
        assert derive_temporal_state(**kwargs, evaluated_at=NOW)["temporal_state"] == expected

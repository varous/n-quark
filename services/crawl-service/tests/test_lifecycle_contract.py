from datetime import UTC, datetime

from crawl_service.lifecycle import temporal_state

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)


def test_shared_contract_fixture():
    fixtures = [
        ({"starts_at": "2026-08-15T10:00:00+00:00"}, "UPCOMING"),
        ({"starts_at": "2026-08-14T10:00:00+00:00", "ends_at": "2026-08-14T14:00:00+00:00"}, "ONGOING"),
        ({"starts_at": "2026-08-13T10:00:00+00:00"}, "PAST"),
        ({"event_date": "2026-08-14", "local_timezone": "Asia/Kolkata"}, "UNKNOWN"),
    ]
    for kwargs, expected in fixtures:
        assert temporal_state(**kwargs, evaluated_at=NOW)["temporal_state"] == expected

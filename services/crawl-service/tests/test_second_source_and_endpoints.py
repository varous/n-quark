from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from crawl_service.config import Settings, settings
from crawl_service.enrichment.registry import SOURCE_API, TEMPORAL_OBSERVATION, Candidate
from crawl_service.main import app

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def test_second_source_folds_into_active_source_sets():
    off = Settings()
    assert "district" not in off.scheduled_capture_source_set
    on = Settings(second_source_capture_enabled=True, second_source_name="district")
    assert "district" in on.scheduled_capture_source_set
    assert "district" in on.capture_enrichment_source_set
    assert "boshow" in on.scheduled_capture_source_set  # Boshow preserved


def test_independence_group_is_per_origin():
    b = Candidate("starts_at", "x", SOURCE_API, "DIRECT_FIELD", 0.9, NOW, origin_source="boshow")
    d = Candidate("starts_at", "x", SOURCE_API, "DIRECT_FIELD", 0.9, NOW, origin_source="district")
    assert b.independence_group == "boshow_origin" and d.independence_group == "district_origin"
    assert b.independence_group != d.independence_group  # cross-source = independent
    t = Candidate("first_ticket_state_seen_at", "x", TEMPORAL_OBSERVATION, "TEMPORAL_INTERVAL", 0.6, NOW)
    assert t.independence_group == "nquark_temporal"


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


def test_reconciliation_endpoints_disabled_by_default(client: TestClient) -> None:
    assert client.post("/v1/internal/reconciliation/probe").status_code == 503
    assert client.post("/v1/internal/reconciliation/run").status_code == 503


def test_reconciliation_reads_available(client: TestClient) -> None:
    assert client.get("/v1/internal/reconciliation/matches").json()["count"] == 0
    m = client.get("/v1/internal/reconciliation/metrics").json()
    assert m["auto_match"] == 0 and m["overlap_rate"] == 0.0
    sr = client.get("/v1/internal/events/event:none/source-records").json()
    assert sr["represented_by"][0]["self"] is True


def test_second_source_defaults_off():
    assert settings.second_source_capture_enabled is False


def test_sync_forwards_source_to_discover(client: TestClient, monkeypatch) -> None:
    """Regression: /sync?source=district must discover FROM district, not the default provider."""
    import crawl_service.routes.capture_schedule as cs

    captured: dict = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"event_refs": ["district-ref-1", "district-ref-2"]}

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            pass

        async def get(self, url, params=None):
            captured["url"] = url
            captured["params"] = params
            return _Resp()

    monkeypatch.setattr(cs.settings, "scheduled_capture_enabled", True)
    monkeypatch.setattr(cs.httpx, "AsyncClient", _Client)
    r = client.post("/v1/internal/capture-schedule/sync", params={"source": "district", "limit": 5})
    assert r.status_code == 200
    assert captured["params"]["source"] == "district"  # source forwarded, not defaulted
    assert r.json()["source"] == "district"
    assert settings.reconciliation_enabled is False

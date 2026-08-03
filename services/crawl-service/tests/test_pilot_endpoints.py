from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from crawl_service.config import settings
from crawl_service.main import app


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as c:
        yield c


def test_pilot_run_disabled_by_default(client: TestClient) -> None:
    assert client.post("/v1/internal/enrichment/pilot/run").status_code == 503


def test_reports_available_and_empty(client: TestClient) -> None:
    runs = client.get("/v1/internal/enrichment/pilot/runs").json()
    assert runs["count"] == 0
    sv = client.get("/v1/internal/enrichment/source-value").json()
    assert sv["pages_attempted"] == 0 and sv["field_breakdown"] == {}
    vc = client.get("/v1/internal/enrichment/venue-coverage").json()
    assert vc["events_with_source_venue_text"] == 0


def test_health_reports_pilot_flags(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["capture_enrichment_enabled"] is False
    # pilot defaults off
    assert settings.capture_enrichment_pilot_enabled is False

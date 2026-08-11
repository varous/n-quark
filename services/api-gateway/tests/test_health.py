from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from api_gateway.config import settings
from api_gateway.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "api-gateway"


def test_platform_status_aggregates_services(monkeypatch) -> None:
    # /v1/platform/status is authenticated (Admin D.2); exercise the aggregation via the local
    # single-context console so no login is needed to test the fan-out itself.
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    mock_response = MagicMock()
    mock_response.json.return_value = {"status": "ok", "service": "crawl-service"}

    with patch("api_gateway.main.httpx.AsyncClient") as mock_client:
        instance = mock_client.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=mock_response)

        response = client.get("/v1/platform/status")

    assert response.status_code == 200
    body = response.json()
    assert "services" in body
    assert body["status"] == "ok"
    # 9 core services + artist-intelligence-service (Phase 5A.2 demand BFF downstream)
    assert len(body["services"]) == 10

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from api_gateway.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "api-gateway"


def test_platform_status_aggregates_services() -> None:
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
    assert len(body["services"]) == 9

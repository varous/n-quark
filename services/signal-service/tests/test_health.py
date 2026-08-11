import pytest
from fastapi.testclient import TestClient

import signal_service.main as main
from signal_service.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "signal-service"


def test_ready_ok_when_observation_reachable(monkeypatch) -> None:
    async def fake_ping(self):
        return True, "ok"

    monkeypatch.setattr(main.ObservationServiceClient, "ping", fake_ping)
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["dependencies"]["observation_service"]["reachable"] is True


def test_ready_503_when_observation_unreachable(monkeypatch) -> None:
    # This is the exact production failure: observation-service not deployed → DNS error. The readiness
    # probe must surface it as 503 with the reason, not report healthy.
    async def fake_ping(self):
        return False, "ConnectError: [Errno -2] Name or service not known"

    monkeypatch.setattr(main.ObservationServiceClient, "ping", fake_ping)
    response = client.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    dep = body["dependencies"]["observation_service"]
    assert dep["reachable"] is False
    assert "Name or service not known" in dep["detail"]
    assert dep["required_by"] == "ticketing /ingest (capture write path)"

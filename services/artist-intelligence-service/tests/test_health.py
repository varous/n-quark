from fastapi.testclient import TestClient

from artist_intelligence_service.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "artist-intelligence-service"
    assert "google_trends_mode" in body

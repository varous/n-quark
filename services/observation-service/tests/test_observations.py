from datetime import UTC, datetime

from fastapi.testclient import TestClient


def test_append_and_list_observations(client: TestClient) -> None:
    payload = {
        "entity": "artist:daft-punk",
        "attribute": "performed_at",
        "value": {"venue": "Madison Square Garden", "city": "New York"},
        "source": "manual-seed",
        "timestamp": "2024-06-01T20:00:00+00:00",
        "confidence": 0.95,
        "evidence": {"url": "https://example.com/event/1"},
        "metadata": {"ingest": "test"},
    }

    create_response = client.post("/v1/observations", json=payload)
    assert create_response.status_code == 201
    created = create_response.json()["observation"]
    assert created["entity"] == payload["entity"]
    assert created["attribute"] == payload["attribute"]
    assert created["metadata"] == payload["metadata"]
    assert "id" in created

    list_response = client.get("/v1/observations/artist:daft-punk")
    assert list_response.status_code == 200
    listed = list_response.json()
    assert listed["count"] == 1
    assert len(listed["observations"]) == 1
    assert listed["observations"][0]["id"] == created["id"]


def test_observations_are_append_only(client: TestClient) -> None:
    payload = {
        "entity": "venue:msg",
        "attribute": "capacity",
        "value": 20789,
        "source": "public-record",
        "confidence": 1.0,
    }
    first = client.post("/v1/observations", json=payload)
    second = client.post("/v1/observations", json=payload)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["observation"]["id"] != second.json()["observation"]["id"]

    listed = client.get("/v1/observations/venue:msg").json()
    assert listed["count"] == 2


def test_get_observation_by_id(client: TestClient) -> None:
    create_response = client.post(
        "/v1/observations",
        json={
            "entity": "event:123",
            "attribute": "announced",
            "value": True,
            "source": "organizer-site",
            "confidence": 0.8,
        },
    )
    observation_id = create_response.json()["observation"]["id"]

    get_response = client.get(f"/v1/observations/by-id/{observation_id}")
    assert get_response.status_code == 200
    assert get_response.json()["id"] == observation_id


def test_get_observation_by_id_not_found(client: TestClient) -> None:
    response = client.get("/v1/observations/by-id/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_default_timestamp_is_set(client: TestClient) -> None:
    before = datetime.now(UTC)
    response = client.post(
        "/v1/observations",
        json={
            "entity": "artist:test",
            "attribute": "genre",
            "value": "electronic",
            "source": "test",
            "confidence": 0.5,
        },
    )
    after = datetime.now(UTC)
    assert response.status_code == 201
    ts = datetime.fromisoformat(response.json()["observation"]["timestamp"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    assert before <= ts <= after


def test_list_recent_observations(client: TestClient) -> None:
    for idx in range(3):
        client.post(
            "/v1/observations",
            json={
                "entity": f"artist:recent-{idx}",
                "attribute": "test",
                "value": idx,
                "source": "test",
                "confidence": 0.5,
            },
        )

    response = client.get("/v1/observations/recent?limit=2")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 3
    assert len(body["observations"]) == 2

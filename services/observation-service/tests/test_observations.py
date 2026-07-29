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


def test_bulk_append_observations(client: TestClient) -> None:
    payload = {
        "observations": [
            {
                "entity": "artist:youtube:chan1",
                "attribute": "subscriber_count",
                "value": 1000,
                "source": "youtube",
                "confidence": 0.95,
            },
            {
                "entity": "artist:youtube:chan1",
                "attribute": "total_view_count",
                "value": 50000,
                "source": "youtube",
                "confidence": 0.95,
            },
        ]
    }
    response = client.post("/v1/observations/bulk", json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["count"] == 2
    assert len(body["observations"]) == 2

    listed = client.get("/v1/observations/artist:youtube:chan1").json()
    assert listed["count"] == 2


def test_provenance_valid_official_api_is_accepted(client: TestClient) -> None:
    response = client.post(
        "/v1/observations",
        json={
            "entity": "artist:youtube:chan1",
            "attribute": "subscriber_count",
            "value": 1000,
            "source": "youtube",
            "confidence": 0.95,
            "metadata": {
                "provenance": {
                    "acquisition_method": "official_api",
                    "legal_basis": "platform_api_tos",
                    "adapter_version": "youtube-v1",
                    "collected_at": "2026-07-29T00:00:00+00:00",
                }
            },
        },
    )
    assert response.status_code == 201


def test_provenance_rejects_pii(client: TestClient) -> None:
    response = client.post(
        "/v1/observations",
        json={
            "entity": "artist:youtube:chan1",
            "attribute": "subscriber_count",
            "value": 1000,
            "source": "youtube",
            "confidence": 0.95,
            "metadata": {
                "provenance": {
                    "acquisition_method": "official_api",
                    "legal_basis": "platform_api_tos",
                    "adapter_version": "youtube-v1",
                    "collected_at": "2026-07-29T00:00:00+00:00",
                    "contains_pii": True,
                }
            },
        },
    )
    assert response.status_code == 422


def test_provenance_rejects_scrape_without_logged_out(client: TestClient) -> None:
    response = client.post(
        "/v1/observations",
        json={
            "entity": "event:some-show",
            "attribute": "announced",
            "value": True,
            "source": "bookmyshow",
            "confidence": 0.6,
            "metadata": {
                "provenance": {
                    "acquisition_method": "public_scrape",
                    "legal_basis": "public_figure_professional",
                    "adapter_version": "bms-v1",
                    "collected_at": "2026-07-29T00:00:00+00:00",
                    "robots_respected": True,
                    "logged_out": False,
                }
            },
        },
    )
    assert response.status_code == 422


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

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from signal_service.adapters.spotify import (
    DEFAULT_MOCK_ARTIST_ID,
    entity_id_for_spotify_artist,
    mock_artist_payload,
    normalize_artist_response,
)
from signal_service.config import settings
from signal_service.main import app
from signal_service.schemas import NormalizedObservation


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_entity_id_for_spotify_artist() -> None:
    assert entity_id_for_spotify_artist("abc123") == "artist:spotify:abc123"


def test_normalize_artist_response_maps_core_signals() -> None:
    artist = mock_artist_payload(DEFAULT_MOCK_ARTIST_ID)
    signals = normalize_artist_response(DEFAULT_MOCK_ARTIST_ID, artist, mock=True)

    assert signals.entity == f"artist:spotify:{DEFAULT_MOCK_ARTIST_ID}"
    assert signals.name == "Daft Punk"
    attributes = {obs.attribute for obs in signals.observations}
    assert attributes == {"display_name", "popularity", "genres", "follower_count"}
    assert all(obs.source == "spotify" for obs in signals.observations)


def test_preview_spotify_artist_mock_mode(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "spotify_mock_mode", True)
    monkeypatch.setattr(settings, "spotify_client_id", "")
    monkeypatch.setattr(settings, "spotify_client_secret", "")

    response = client.get(f"/v1/signals/spotify/artists/{DEFAULT_MOCK_ARTIST_ID}/preview")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Daft Punk"
    assert body["mock"] is True
    assert len(body["observations"]) >= 4


def test_ingest_spotify_artist_persists_observations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "spotify_mock_mode", True)

    fake_persisted = [
        {"observation": {"id": f"00000000-0000-0000-0000-00000000000{i}"}} for i in range(4)
    ]

    with patch(
        "signal_service.routes.spotify.ObservationServiceClient.append_observations",
        new_callable=AsyncMock,
        return_value=fake_persisted,
    ) as append_mock:
        response = client.post(f"/v1/signals/spotify/artists/{DEFAULT_MOCK_ARTIST_ID}/ingest")

    assert response.status_code == 200
    body = response.json()
    assert body["observations_created"] == 4
    assert body["entity"] == entity_id_for_spotify_artist(DEFAULT_MOCK_ARTIST_ID)
    append_mock.assert_awaited_once()
    sent: list[NormalizedObservation] = append_mock.await_args.args[0]
    assert len(sent) == 4
    assert sent[0].entity.startswith("artist:spotify:")


def test_health_reports_mock_mode(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "spotify_mock_mode", True)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["spotify_mock"] == "true"

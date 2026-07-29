from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from signal_service.adapters.youtube import (
    DEFAULT_MOCK_CHANNEL_ID,
    entity_id_for_youtube_channel,
    mock_channel_payload,
    normalize_channel_response,
)
from signal_service.config import settings
from signal_service.main import app
from signal_service.schemas import NormalizedObservation


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_entity_id_for_youtube_channel() -> None:
    assert entity_id_for_youtube_channel("abc123") == "artist:youtube:abc123"


def test_normalize_channel_response_maps_core_signals() -> None:
    channel = mock_channel_payload(DEFAULT_MOCK_CHANNEL_ID)
    signals = normalize_channel_response(
        DEFAULT_MOCK_CHANNEL_ID, channel, mock=True, trending_rank=2
    )

    assert signals.entity == f"artist:youtube:{DEFAULT_MOCK_CHANNEL_ID}"
    assert signals.name == "T-Series"
    attributes = {obs.attribute for obs in signals.observations}
    assert attributes == {
        "display_name",
        "subscriber_count",
        "total_view_count",
        "video_count",
        "trending_rank_in",
    }
    assert all(obs.source == "youtube" for obs in signals.observations)


def test_every_observation_carries_official_api_provenance() -> None:
    channel = mock_channel_payload(DEFAULT_MOCK_CHANNEL_ID)
    signals = normalize_channel_response(DEFAULT_MOCK_CHANNEL_ID, channel, mock=True)

    for obs in signals.observations:
        provenance = obs.metadata["provenance"]
        assert provenance["acquisition_method"] == "official_api"
        assert provenance["data_subject_type"] == "entity"
        assert provenance["contains_pii"] is False
        assert provenance["adapter_version"] == "youtube-v1"


def test_numeric_stats_are_coerced_to_int() -> None:
    signals = normalize_channel_response(
        "chan1",
        {"snippet": {"title": "X"}, "statistics": {"subscriberCount": "42"}},
        mock=False,
    )
    subs = next(o for o in signals.observations if o.attribute == "subscriber_count")
    assert subs.value == 42
    assert isinstance(subs.value, int)


def test_preview_youtube_channel_mock_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_mock_mode", True)
    monkeypatch.setattr(settings, "youtube_api_key", "")

    response = client.get(f"/v1/signals/youtube/channels/{DEFAULT_MOCK_CHANNEL_ID}/preview")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "T-Series"
    assert body["mock"] is True
    assert len(body["observations"]) >= 4


def test_ingest_youtube_channel_persists_observations(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "youtube_mock_mode", True)

    fake_persisted = [
        {"id": f"00000000-0000-0000-0000-00000000000{i}"} for i in range(5)
    ]

    with patch(
        "signal_service.routes.youtube.ObservationServiceClient.append_observations",
        new_callable=AsyncMock,
        return_value=fake_persisted,
    ) as append_mock:
        response = client.post(
            f"/v1/signals/youtube/channels/{DEFAULT_MOCK_CHANNEL_ID}/ingest"
        )

    assert response.status_code == 200
    body = response.json()
    assert body["observations_created"] == 5
    assert body["entity"] == entity_id_for_youtube_channel(DEFAULT_MOCK_CHANNEL_ID)
    append_mock.assert_awaited_once()
    sent: list[NormalizedObservation] = append_mock.await_args.args[0]
    assert sent[0].entity.startswith("artist:youtube:")


def test_ingest_with_trace_returns_pipeline_trace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "youtube_mock_mode", True)

    stored = [{"id": f"00000000-0000-0000-0000-00000000000{i}"} for i in range(5)]
    with patch(
        "signal_service.routes.youtube.ObservationServiceClient.append_observations",
        new_callable=AsyncMock,
        return_value=stored,
    ):
        response = client.post(
            f"/v1/signals/youtube/channels/{DEFAULT_MOCK_CHANNEL_ID}/ingest?trace=true"
        )

    assert response.status_code == 200
    trace = response.json()["trace"]
    assert [record["stage"] for record in trace] == ["ingestion", "observation"]
    # ingestion stage carries the normalized observation payloads it produced
    assert len(trace[0]["output"]) == 5
    assert "provenance" in trace[0]["output"][0]["metadata"]
    # observation stage carries what the store added
    assert "uuid" in trace[1]["added"]


def test_ingest_without_trace_omits_trace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "youtube_mock_mode", True)
    with patch(
        "signal_service.routes.youtube.ObservationServiceClient.append_observations",
        new_callable=AsyncMock,
        return_value=[],
    ):
        response = client.post(
            f"/v1/signals/youtube/channels/{DEFAULT_MOCK_CHANNEL_ID}/ingest"
        )
    assert "trace" not in response.json()


def test_health_reports_youtube_mock_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "youtube_mock_mode", True)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["youtube_mock"] == "true"

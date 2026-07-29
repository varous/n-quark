from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from signal_service.adapters.youtube import (
    DEFAULT_MOCK_CHANNEL_ID,
    entity_id_for_youtube_channel,
    mock_channel_payload,
    normalize_channel_response,
)
from signal_service.classification import classify_youtube_channel
from signal_service.config import settings
from signal_service.main import app
from signal_service.schemas import NormalizedObservation


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_entity_id_is_type_neutral() -> None:
    # The adapter must not assert a type — classification decides it later.
    assert entity_id_for_youtube_channel("abc123") == "youtube:channel:abc123"


def test_classify_tseries_as_label_not_artist() -> None:
    c = classify_youtube_channel(
        DEFAULT_MOCK_CHANNEL_ID, "T-Series", mock_channel_payload(DEFAULT_MOCK_CHANNEL_ID)
    )
    assert c.entity_type == "label"
    assert c.method == "registry"
    assert c.confidence >= 0.9


def test_classify_label_by_name_heuristic() -> None:
    c = classify_youtube_channel(
        "UCsomethingelse", "Believe Records", {"statistics": {"videoCount": "5000"}}
    )
    assert c.entity_type == "label"
    assert c.method == "heuristic"


def test_classify_unknown_defaults_to_low_confidence_artist() -> None:
    c = classify_youtube_channel("UCzzz", "Some Person", {"statistics": {"videoCount": "40"}})
    assert c.entity_type == "artist"
    assert c.method == "default"
    assert c.needs_review is True


def test_normalize_channel_response_maps_core_signals() -> None:
    channel = mock_channel_payload(DEFAULT_MOCK_CHANNEL_ID)
    signals = normalize_channel_response(
        DEFAULT_MOCK_CHANNEL_ID, channel, mock=True, trending_rank=2
    )

    assert signals.entity == f"youtube:channel:{DEFAULT_MOCK_CHANNEL_ID}"
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


@pytest.fixture()
def _stub_resolve(monkeypatch: pytest.MonkeyPatch):
    """Stub entity-service resolution so ingest tests don't hit the network."""
    async def fake_resolve(self, **kwargs):
        etype = kwargs["entity_type"]
        return {"canonical_id": f"{etype}:t-series", "created": True, "alias_linked": True}

    monkeypatch.setattr(
        "signal_service.routes.youtube.EntityServiceClient.resolve", fake_resolve
    )


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


def test_ingest_classifies_tseries_as_label(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _stub_resolve: None,
) -> None:
    monkeypatch.setattr(settings, "youtube_mock_mode", True)

    fake_persisted = [{"id": f"00000000-0000-0000-0000-00000000000{i}"} for i in range(6)]
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
    # Source handle is type-neutral; classification decides the type; resolves to a LABEL.
    assert body["source_entity"] == entity_id_for_youtube_channel(DEFAULT_MOCK_CHANNEL_ID)
    assert body["classification"]["entity_type"] == "label"
    assert body["canonical_id"] == "label:t-series"

    append_mock.assert_awaited_once()
    sent: list[NormalizedObservation] = append_mock.await_args.args[0]
    assert sent[0].entity.startswith("youtube:channel:")
    # a candidate_entity_type observation is appended alongside the signals
    assert any(o.attribute == "candidate_entity_type" for o in sent)


def test_ingest_with_trace_returns_four_stage_pipeline(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _stub_resolve: None,
) -> None:
    monkeypatch.setattr(settings, "youtube_mock_mode", True)

    stored = [{"id": f"00000000-0000-0000-0000-00000000000{i}"} for i in range(6)]
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
    assert [r["stage"] for r in trace] == [
        "ingestion",
        "classification",
        "observation",
        "entity",
    ]
    assert "provenance" in trace[0]["output"][0]["metadata"]
    # classification stage names the inferred type
    assert trace[1]["output"]["attribute"] == "candidate_entity_type"
    assert trace[1]["output"]["value"] == "label"
    # entity stage resolves to the classified (label) type
    assert trace[3]["output"]["canonical_id"] == "label:t-series"


def test_ingest_without_trace_omits_trace(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _stub_resolve: None,
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

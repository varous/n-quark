from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from signal_service.adapters.google_trends import (
    MockGoogleTrendsProvider,
    SerpApiProvider,
    TrendsRaw,
    derive_momentum,
    entity_id_for_query,
    get_provider,
    normalize_trends,
)
from signal_service.adapters.musicbrainz import MusicBrainzClient
from signal_service.config import settings
from signal_service.main import app
from signal_service.schemas import NormalizedObservation


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def _offline_trends_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Run Trends classify->resolve->fold->graph offline with stubbed downstream services."""
    monkeypatch.setattr(settings, "google_trends_provider", "mock")
    monkeypatch.setattr(settings, "musicbrainz_mock_mode", True)
    MusicBrainzClient._cache.clear()

    async def fake_resolve(self, **kwargs):
        etype = kwargs["entity_type"]
        slug = kwargs["display_name"].lower().replace(" ", "-")
        return {"canonical_id": f"{etype}:{slug}", "created": True, "alias_linked": True}

    async def fake_link(self, **kwargs):
        return {"id": kwargs["canonical_id"], "aliases": kwargs["aliases"]}

    async def fake_projection(self, projection):
        return {"nodes": len(projection.nodes), "edges": len(projection.edges)}

    monkeypatch.setattr(
        "signal_service.routes.google_trends.EntityServiceClient.resolve", fake_resolve
    )
    monkeypatch.setattr(
        "signal_service.routes.google_trends.EntityServiceClient.link_aliases", fake_link
    )
    monkeypatch.setattr(
        "signal_service.routes.google_trends.GraphServiceClient.upsert_projection", fake_projection
    )


def test_entity_id_is_type_neutral() -> None:
    assert entity_id_for_query("Arijit Singh") == "google:query:arijit-singh"


def test_provider_defaults_to_mock_without_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_trends_provider", "dataforseo")
    monkeypatch.setattr(settings, "dataforseo_login", "")
    assert get_provider().name == "mock"


def test_derive_momentum_direction_not_level() -> None:
    assert derive_momentum([60, 62, 65, 63, 78, 82, 88, 90])[0] == "rising"
    assert derive_momentum([90, 88, 82, 78, 63, 60, 55, 50])[0] == "falling"
    assert derive_momentum([50, 51, 49, 50, 51, 50, 49, 50])[0] == "steady"
    _, breakout = derive_momentum([10, 10, 10, 10, 12, 11, 13, 60])
    assert breakout is True


async def test_normalize_emits_geographic_and_identity_signals() -> None:
    raw = await MockGoogleTrendsProvider().fetch("Arijit Singh", "IN")
    signals = normalize_trends(raw, "mock")

    attrs = {o.attribute for o in signals.observations}
    assert "search_interest_by_region" in attrs
    assert "search_top_regions" in attrs
    assert "search_momentum" in attrs
    assert "google_kg_mid" in attrs  # identity cross-reference, like MBID
    assert all(o.source == "google_trends" for o in signals.observations)

    top = next(o for o in signals.observations if o.attribute == "search_top_regions")
    assert top.value[0] == "West Bengal"  # geographic distribution is the crown-jewel signal


def test_serpapi_extracts_kg_mid_from_related_topics() -> None:
    # RELATED_TOPICS.top leads with the queried entity; pick the artist-typed exact match.
    payload = {
        "related_topics": {
            "top": [
                {"topic": {"value": "/m/08hr72", "title": "Arijit Singh", "type": "Indian singer and composer"}},
                {"topic": {"value": "/m/074ft", "title": "Song", "type": "Composition type"}},
            ]
        }
    }
    assert SerpApiProvider()._extract_mid(payload, "Arijit Singh") == "/m/08hr72"


def test_serpapi_mid_none_when_no_entity_match() -> None:
    payload = {"related_topics": {"top": [{"topic": {"value": "/m/074ft", "title": "Song", "type": "Composition type"}}]}}
    assert SerpApiProvider()._extract_mid(payload, "Arijit Singh") is None


def test_by_region_observation_carries_aggregator_provenance() -> None:
    raw = TrendsRaw(query="X", region="IN", interest_by_region={"Delhi": 100})
    signals = normalize_trends(raw, "mock")
    region_obs = next(o for o in signals.observations if o.attribute == "search_interest_by_region")
    prov = region_obs.metadata["provenance"]
    assert prov["acquisition_method"] == "aggregator_api"
    assert prov["contains_pii"] is False


def test_preview_google_trends_mock(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_trends_provider", "mock")
    response = client.get("/v1/signals/google-trends/artists/Arijit Singh/preview")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "mock"
    assert body["region"] == "IN"
    assert body["mock"] is True


def test_ingest_with_trace(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, _offline_trends_pipeline: None
) -> None:
    stored = [{"id": f"00000000-0000-0000-0000-00000000000{i}"} for i in range(6)]
    with patch(
        "signal_service.routes.google_trends.ObservationServiceClient.append_observations",
        new_callable=AsyncMock,
        return_value=stored,
    ):
        response = client.post(
            "/v1/signals/google-trends/artists/Arijit Singh/ingest?trace=true"
        )
    assert response.status_code == 200
    body = response.json()
    assert body["entity"] == "google:query:arijit-singh"
    # Trends is now a full classify->resolve->fold->graph pipeline, mirroring YouTube.
    assert [r["stage"] for r in body["trace"]] == [
        "ingestion",
        "classification",
        "observation",
        "entity",
        "graph",
    ]
    # the query resolves to a canonical artist and demand edges attach to it (not the handle)
    assert body["canonical_id"] == "artist:arijit-singh"
    graph_out = body["trace"][4]["output"]
    assert graph_out["nodes"][0]["id"] == "artist:arijit-singh"
    assert graph_out["nodes"][0]["type"] == "artist"
    assert any(e["relationship"] == "STRONG_IN" for e in graph_out["edges"])
    # the KG mID folds in as an alias — the cross-source backbone
    assert any(a.startswith("kgmid:") for a in body["aliases_linked"])


def test_health_reports_trends_provider(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_trends_provider", "mock")
    response = client.get("/health")
    assert response.json()["trends_provider"] == "mock"


def test_sent_observations_key_on_source_handle(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, _offline_trends_pipeline: None
) -> None:
    with patch(
        "signal_service.routes.google_trends.ObservationServiceClient.append_observations",
        new_callable=AsyncMock,
        return_value=[],
    ) as append_mock:
        client.post("/v1/signals/google-trends/artists/Diljit Dosanjh/ingest")
    sent: list[NormalizedObservation] = append_mock.await_args.args[0]
    # signals + the appended classification observation all key on the type-neutral handle
    assert all(o.entity == "google:query:diljit-dosanjh" for o in sent)
    assert any(o.attribute == "candidate_entity_type" for o in sent)

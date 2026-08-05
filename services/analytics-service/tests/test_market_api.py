"""Market read-model routes — envelope/scope, trace, pagination, 404, canonicalization, filters.

The datasource is stubbed with a fixed in-memory snapshot (no network)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from analytics_service import readmodels as rm
from analytics_service.deps import get_datasource
from analytics_service.main import app
from analytics_service.projection import Canonicalizer

NOW = datetime(2026, 8, 5, tzinfo=UTC)


def _dataset() -> rm.Dataset:
    entities = {
        "artist:skinny-mos": rm.EntityMeta("artist:skinny-mos", "ARTIST", "Skinny Mos", "CANONICAL"),
        "venue:grand-hall--kolkata": rm.EntityMeta("venue:grand-hall--kolkata", "VENUE", "Grand Hall", "CANONICAL", city="Kolkata"),
        "venue:legacy-hall": rm.EntityMeta("venue:legacy-hall", "VENUE", "legacy", "SUPERSEDED", superseded=True),
        "series:the-abomination": rm.EntityMeta("series:the-abomination", "EVENT_SERIES", "The Abomination", "CANONICAL", strong_series_marker=True),
    }
    canon = Canonicalizer(supersession={"venue:legacy-hall": "venue:grand-hall--kolkata"},
                          identity_states={k: v.identity_state for k, v in entities.items()},
                          known_ids=set(entities))
    events = [
        rm.ObservedEvent("event:e1", "boshow", city="Kolkata", region="region:west-bengal",
                         starts_at="2026-09-01T20:00:00+00:00", price_min=599.0, currency="INR",
                         capture_count=3, distinct_state_count=2, transition_count=2,
                         artists=["artist:skinny-mos"], venues=["venue:legacy-hall"],
                         series=["series:the-abomination"],
                         transition_types={"PUBLIC_FILL_RATIO_CHANGED": 1}),
        rm.ObservedEvent("event:e2", "district", city="Mumbai",
                         starts_at="2026-07-01T20:00:00+00:00", price_min=999.0, currency="INR",
                         capture_count=1, artists=["artist:skinny-mos"]),
    ]
    return rm.Dataset(events=events, entities=entities, canonicalizer=canon,
                      sources=["boshow", "district"], now=NOW)


class _StubDS:
    def __init__(self, dataset): self._d = dataset
    async def load(self, *, now=None): return self._d


@pytest.fixture()
def client():
    app.dependency_overrides[get_datasource] = lambda: _StubDS(_dataset())
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_regions_list_has_scope_and_items(client):
    body = client.get("/v1/analytics/market/regions").json()
    assert body["count"] == 2 and "observation_scope" in body["scope"]
    assert {r["region"] for r in body["items"]} == {"region:west-bengal", "city:Mumbai"}


def test_region_detail_404_when_absent(client):
    assert client.get("/v1/analytics/market/regions/region:nowhere").status_code == 404


def test_artist_detail_counts_and_folds(client):
    body = client.get("/v1/analytics/market/artists/artist:skinny-mos").json()
    assert body["observed_event_count"] == 2
    # e1's legacy venue is folded to canonical
    assert {v["canonical_entity_id"] for v in body["venues"]} == {"venue:grand-hall--kolkata"}


def test_artist_404(client):
    assert client.get("/v1/analytics/market/artists/artist:nobody").status_code == 404


def test_canonicalize_endpoint(client):
    body = client.get("/v1/analytics/market/canonicalize/venue:legacy-hall").json()
    assert body["canonical_entity_id"] == "venue:grand-hall--kolkata"
    assert body["resolution_path"] == ["venue:legacy-hall", "venue:grand-hall--kolkata"]


def test_trace_included_only_when_requested(client):
    assert "trace" not in client.get("/v1/analytics/market/regions").json()
    traced = client.get("/v1/analytics/market/regions?trace=true").json()
    assert "trace" in traced
    assert traced["trace"]["superseded_identities_deduplicated"] == ["venue:legacy-hall"]


def test_source_filter_and_pagination(client):
    body = client.get("/v1/analytics/market/artists?source=district&limit=1&offset=0").json()
    assert body["limit"] == 1 and body["count"] >= 1
    # district-only → skinny-mos still appears (has an e2 in district)
    assert body["items"][0]["source_distribution"] == {"district": 1}


def test_observation_quality_and_commercial_state(client):
    q = client.get("/v1/analytics/market/observation-quality?by=source").json()
    assert q["overall"]["tracked_events"] == 2 and set(q["by_source"]) == {"boshow", "district"}
    c = client.get("/v1/analytics/market/commercial-state").json()
    assert c["events_with_price_observations"] == 2
    assert set(c["price_by_source"]) == {"boshow", "district"}


def test_series_list_strong_only(client):
    body = client.get("/v1/analytics/market/series").json()
    assert [r["canonical_entity_id"] for r in body["items"]] == ["series:the-abomination"]


def test_legacy_scoring_endpoints_still_present(client):
    # compatibility: the old demand endpoints remain mounted alongside the new market read models
    paths = set(app.openapi()["paths"])
    assert "/v1/analytics/artists/{canonical_id}" in paths
    assert "/v1/analytics/regions/{region_id}" in paths
    assert "/v1/analytics/market/observation-quality" in paths

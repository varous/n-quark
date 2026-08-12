"""Phase 5B.2 — product catalog BFF: first-class Artists & Venues from the canonical registry.

Proves the product Artist/Venue lists come from crawl's authoritative canonical enumeration (never raw
graph artist-type nodes, so source-handle projections cannot inflate counts), that monitoring state is
merged in one batch call, and that the list degrades gracefully when artist-intelligence is down.
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from api_gateway.admin.catalog import CatalogAdminService
from api_gateway.admin.deps import get_catalog_service
from api_gateway.admin.gateway_client import Down, DownstreamGateway
from api_gateway.config import settings
from api_gateway.main import app

ENTITIES = "/v1/internal/entity-resolution/entities"
SUMM = "/v1/internal/artists/summaries"


class FakeGateway(DownstreamGateway):
    def __init__(self, responses: dict, unavailable: set[str] | None = None):
        super().__init__(base_urls={"crawl": "http://crawl", "artist_intelligence": "http://ai"})
        self._responses = responses
        self._unavailable = unavailable or set()

    async def request(self, service, method, path, *, params=None, json=None):
        key = f"{service}:{path}"
        if key in self._unavailable:
            return Down(available=False, status=0, error="down")
        if key not in self._responses:
            return Down(available=True, status=404, data=None)
        return Down(available=True, status=200, data=self._responses[key])


def _svc(responses=None, unavailable=None) -> CatalogAdminService:
    return CatalogAdminService(FakeGateway(responses or {}, unavailable))


@pytest.fixture()
def local(monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# the canonical registry returns exactly the two canonical artists (NOT the boshow:artist:* projections)
CANONICAL_ARTISTS = {f"crawl:{ENTITIES}": {"count": 2, "entities": [
    {"canonical_entity_id": "artist:arijit-singh", "canonical_name": "Arijit Singh",
     "entity_type": "ARTIST", "linked_event_count": 3, "sources": ["boshow"], "last_observed": "2026-08-10"},
    {"canonical_entity_id": "artist:anuv-jain", "canonical_name": "Anuv Jain",
     "entity_type": "ARTIST", "linked_event_count": 0, "sources": ["district"], "last_observed": None}]}}
SUMMARIES = {f"artist_intelligence:{SUMM}": {"summaries": {
    "artist:arijit-singh": {"watching": True, "watch_status": "WATCHING", "youtube_identity_state": "PENDING",
                            "owned_videos": 0, "has_demand_data": True, "moving_content_count": 0}}}}


def test_artists_use_canonical_registry_not_graph(local):
    app.dependency_overrides[get_catalog_service] = lambda: _svc({**CANONICAL_ARTISTS, **SUMMARIES})
    body = local.get("/admin/v1/catalog/artists").json()
    assert body["available"] is True and body["count"] == 2
    ids = [a["canonical_artist_id"] for a in body["artists"]]
    assert ids == ["artist:arijit-singh", "artist:anuv-jain"]
    # a source-projection node id could never appear — the list is built from canonical rows only
    assert not any(i.startswith("boshow:") for i in ids)


def test_monitoring_is_merged(local):
    app.dependency_overrides[get_catalog_service] = lambda: _svc({**CANONICAL_ARTISTS, **SUMMARIES})
    arijit = next(a for a in local.get("/admin/v1/catalog/artists").json()["artists"]
                  if a["canonical_artist_id"] == "artist:arijit-singh")
    assert arijit["watching"] is True and arijit["has_demand_data"] is True
    assert arijit["youtube_identity_state"] == "PENDING" and arijit["youtube_verified"] is False


def test_watching_filter(local):
    app.dependency_overrides[get_catalog_service] = lambda: _svc({**CANONICAL_ARTISTS, **SUMMARIES})
    body = local.get("/admin/v1/catalog/artists?watching=true").json()
    assert [a["canonical_artist_id"] for a in body["artists"]] == ["artist:arijit-singh"]


def test_artists_degrade_when_monitoring_down(local):
    app.dependency_overrides[get_catalog_service] = lambda: _svc(
        CANONICAL_ARTISTS, unavailable={f"artist_intelligence:{SUMM}"})
    body = local.get("/admin/v1/catalog/artists").json()
    assert body["available"] is True and body["monitoring_available"] is False
    assert len(body["artists"]) == 2                       # identity still renders
    assert body["artists"][0]["youtube_identity_state"] is None


def test_venues_use_canonical_registry(local):
    app.dependency_overrides[get_catalog_service] = lambda: _svc({f"crawl:{ENTITIES}": {
        "count": 1, "entities": [{"canonical_entity_id": "venue:antisocial", "canonical_name": "antiSOCIAL",
                                  "entity_type": "VENUE", "linked_event_count": 4, "sources": ["skillbox"]}]}})
    body = local.get("/admin/v1/catalog/venues").json()
    assert body["available"] is True
    assert body["venues"][0]["canonical_venue_id"] == "venue:antisocial"
    assert body["venues"][0]["events_observed"] == 4


def test_catalog_is_read_only(local):
    app.dependency_overrides[get_catalog_service] = lambda: _svc({})
    assert local.post("/admin/v1/catalog/artists").status_code == 405
    assert local.post("/admin/v1/catalog/venues").status_code == 405

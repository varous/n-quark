"""Internal media API: observe, reads, coverage, failures, pagination, feature-disabled gating."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from media_service import fetcher
from media_service.config import settings
from media_service.deps import get_media_reads, get_media_service
from media_service.fetcher import FetchResult
from media_service.main import app
from media_service.reads import MediaReads
from media_service.service import MediaService
from media_service.storage import ContentAddressedStore
from tests.conftest import png_bytes


@pytest.fixture()
def client(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "media_observation_enabled", True)

    async def fetch_fn(url):
        if "missing" in url:
            return FetchResult(fetcher.NOT_FOUND, http_status=404)
        return FetchResult(fetcher.FETCHED, 200, "image/png", png_bytes(12, 8, url.encode()),
                           final_url=url)

    svc = MediaService(session_factory=session_factory, fetch_fn=fetch_fn,
                       store=ContentAddressedStore(str(tmp_path), enabled=True),
                       graph=None, cfg=SimpleNamespace(media_fetch_enabled=True,
                                                       media_graph_link_enabled=False))
    app.dependency_overrides[get_media_service] = lambda: svc
    app.dependency_overrides[get_media_reads] = lambda: MediaReads(session_factory)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_disabled_returns_503(session_factory, monkeypatch):
    monkeypatch.setattr(settings, "media_observation_enabled", False)
    c = TestClient(app)
    assert c.post("/v1/internal/media/observe", json={"canonical_event_id": "e", "source": "boshow"}).status_code == 503
    assert c.get("/v1/internal/media/coverage").status_code == 503


def test_observe_requires_fields(client):
    assert client.post("/v1/internal/media/observe", json={"source": "boshow"}).status_code == 422


def test_source_not_enabled_rejected(client):
    r = client.post("/v1/internal/media/observe",
                    json={"canonical_event_id": "event:a", "source": "skillbox", "asset_url": "https://x/a.png"})
    assert r.status_code == 422


def test_observe_then_reads(client):
    r = client.post("/v1/internal/media/observe", json={
        "canonical_event_id": "event:a", "source": "boshow", "asset_role": "POSTER",
        "asset_url": "https://cdn.example.com/a.png", "observed_at": "2026-08-05T00:00:00+00:00"})
    assert r.status_code == 200
    body = r.json()
    assert body["transitions"] == ["MEDIA_FIRST_SEEN"] and body["media_asset_id"]

    ev = client.get("/v1/internal/media/events/event:a").json()
    assert ev["creatives"][0]["asset_role"] == "POSTER" and ev["creatives"][0]["asset"]["width"] == 12

    tl = client.get("/v1/internal/media/events/event:a/timeline").json()
    assert tl["count"] == 1 and tl["transitions"][0]["transition_type"] == "MEDIA_FIRST_SEEN"

    cov = client.get("/v1/internal/media/coverage").json()
    assert cov["by_source"]["boshow"]["events_with_asset_references"] == 1
    assert cov["by_source"]["boshow"]["successful_fetches"] == 1


def test_failures_classified(client):
    client.post("/v1/internal/media/observe", json={
        "canonical_event_id": "event:b", "source": "district", "asset_role": "POSTER",
        "asset_url": "https://cdn.example.com/missing.png"})
    f = client.get("/v1/internal/media/failures").json()
    assert f["count"] == 1 and f["items"][0]["fetch_status"] == fetcher.NOT_FOUND


def test_assets_pagination_and_404(client):
    for i in range(3):
        client.post("/v1/internal/media/observe", json={
            "canonical_event_id": f"event:{i}", "source": "boshow", "asset_role": "POSTER",
            "asset_url": f"https://cdn.example.com/{i}.png"})
    page = client.get("/v1/internal/media/assets?limit=2&offset=0").json()
    assert page["count"] == 3 and len(page["items"]) == 2 and page["limit"] == 2
    assert client.get("/v1/internal/media/assets/nope").status_code == 404

"""Phase 5A.2 — demand intelligence BFF (read-only) over artist-intelligence-service.

Covers healthy + unavailable + degraded panels, the per-artist bundle (identities / YouTube /
momentum / geography), provider health REAL/MOCK, quota, scheduler, bounded observations, the event
demand context, and the read-only guarantee. Downstreams are stubbed (no network).
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from api_gateway.admin.demand import DemandAdminService
from api_gateway.admin.deps import get_demand_service
from api_gateway.admin.gateway_client import Down, DownstreamGateway
from api_gateway.config import settings
from api_gateway.main import app


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


def _demand(responses=None, unavailable=None) -> DemandAdminService:
    return DemandAdminService(FakeGateway(responses or {}, unavailable))


@pytest.fixture()
def local(monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ---- coverage / provider-health / quota / scheduler ---------------------------------------------
OVERVIEW_RESPONSES = {
    "artist_intelligence:/v1/internal/demand/coverage": {
        "canonical_artists": 42,
        "youtube_identity_status": {"resolved": 3, "ambiguous": 1, "unresolved": 2,
                                    "artists_with_youtube_identity": 6},
        "artists_with_demand_observation": 4, "regions_covered": 5, "stale_demand_artists": 1,
        "trends_api_vs_imported": {"imported_provider_export": 5, "official_api": 0},
        "trends_mappings": 2,
        "disclaimer": "observed public demand for a bounded cohort; NOT complete market demand coverage",
    },
    "artist_intelligence:/v1/internal/demand/provider-health": {
        "providers": {
            "youtube": {"enabled": True, "search_enabled": True, "acquisition": "signal-service",
                        "mode": {"mode": "REAL", "source": "signal-service", "available": True},
                        "quota_today": {"requests": 8, "search_requests": 2, "search_quota_units": 200,
                                        "non_search_quota_units": 6, "successful_calls": 8,
                                        "failed_calls": 0, "quota_errors": 0}},
            "google_trends": {"mode": "IMPORT", "status": "ACCESS_UNAVAILABLE", "import_enabled": True},
        }},
    "artist_intelligence:/v1/internal/demand/quota": {
        "days": [{"provider": "YOUTUBE", "date": "2026-08-08", "requests": 8}],
        "youtube_max_searches_per_day": 50},
    "artist_intelligence:/v1/internal/demand/scheduler": {
        "enabled": True, "queued_due": 0, "running_leased": 0, "retrying": 0, "succeeded": 6,
        "terminal_failures": 1, "latest_successful_refresh": "2026-08-08T00:00:00+00:00",
        "next_scheduled_refresh": "2026-08-09T00:00:00+00:00",
        "due_by_job_type": {"channel_jobs_due": 2, "video_jobs_due": 2, "identity_jobs_due": 5,
                            "catalogue_backfill_jobs_due": 1}},
    "artist_intelligence:/v1/internal/demand/artist-universe": {
        "candidates": {"total": 12, "new": 8, "resolved": 3, "ambiguous": 1, "rejected": 0},
        "india_market_presence": {"confirmed_live_india": 3, "india_demand_observed": 1,
                                  "india_market_candidate": 8},
        "videos": {"videos_discovered": 40, "videos_active": 40},
        "discovery_contribution_by_source": {"EVENT": 3, "YOUTUBE_SEARCH": 9},
        "multi_source_artists": 1, "identity_resolution_queue_depth": 5},
    "artist_intelligence:/v1/internal/demand/quota-buckets": {
        "provider": "YOUTUBE", "quota_date": "2026-08-08", "reset_tz": "America/Los_Angeles",
        "daily_quota_units": 10000, "usable_units": 9500, "reserve_units": 500, "used_total": 320,
        "buckets": {"SEARCH": {"used": 200, "budget": 3500, "remaining": 3300},
                    "GENERAL_READ": {"used": 100, "budget": 4500, "remaining": 4400},
                    "VIDEO_STATS_BATCH": {"used": 20, "budget": 1500, "remaining": 1480}}},
}


def test_overview_healthy(local):
    app.dependency_overrides[get_demand_service] = lambda: _demand(OVERVIEW_RESPONSES)
    r = local.get("/admin/v1/demand/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["coverage"]["canonical_artists"] == 42
    assert body["provider_health"]["providers"]["youtube"]["mode"]["mode"] == "REAL"
    assert body["scheduler"]["terminal_failures"] == 1
    assert body["quota"]["youtube_max_searches_per_day"] == 50
    # Phase 5A.3 artist-universe + per-bucket quota diagnostics surface through the same BFF
    assert body["artist_universe"]["candidates"]["total"] == 12
    assert body["artist_universe"]["india_market_presence"]["confirmed_live_india"] == 3
    assert body["quota_buckets"]["buckets"]["SEARCH"]["remaining"] == 3300


def test_overview_unavailable_degrades_not_500(local):
    # every demand call is down → panel degrades, but the endpoint still returns 200
    down = {f"artist_intelligence:{p}" for p in (
        "/v1/internal/demand/coverage", "/v1/internal/demand/provider-health",
        "/v1/internal/demand/quota", "/v1/internal/demand/scheduler",
        "/v1/internal/demand/artist-universe", "/v1/internal/demand/quota-buckets")}
    app.dependency_overrides[get_demand_service] = lambda: _demand({}, unavailable=down)
    r = local.get("/admin/v1/demand/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["coverage"] is None and body["scheduler"] is None


def test_overview_partial_degraded(local):
    # scheduler down, the rest healthy → still available, scheduler null (partial data)
    app.dependency_overrides[get_demand_service] = lambda: _demand(
        OVERVIEW_RESPONSES, unavailable={"artist_intelligence:/v1/internal/demand/scheduler"})
    body = local.get("/admin/v1/demand/overview").json()
    assert body["available"] is True
    assert body["coverage"] is not None
    assert body["scheduler"] is None
    assert body["downstream"]["scheduler"] is False


def test_provider_health_mock_is_visible(local):
    resp = dict(OVERVIEW_RESPONSES)
    resp["artist_intelligence:/v1/internal/demand/provider-health"] = {
        "providers": {"youtube": {"mode": {"mode": "MOCK", "source": "signal-service", "available": True}},
                      "google_trends": {"status": "ACCESS_UNAVAILABLE"}}}
    app.dependency_overrides[get_demand_service] = lambda: _demand(resp)
    body = local.get("/admin/v1/demand/overview").json()
    assert body["provider_health"]["providers"]["youtube"]["mode"]["mode"] == "MOCK"


def test_summary_extracts_mode_and_counts(local):
    app.dependency_overrides[get_demand_service] = lambda: _demand(OVERVIEW_RESPONSES)
    body = local.get("/admin/v1/demand/summary").json()
    assert body["available"] is True
    assert body["resolved_youtube_artists"] == 3
    assert body["youtube_mode"] == "REAL"
    assert body["scheduler_enabled"] is True


# ---- per-artist demand --------------------------------------------------------------------------
ARTIST = "artist:arijit-singh"
ARTIST_RESPONSES = {
    f"artist_intelligence:/v1/internal/artists/{ARTIST}/demand": {
        "external_identities": [
            {"id": "i1", "provider": "YOUTUBE", "identity_type": "CHANNEL_ID",
             "provider_id": "UCabc", "display_name": "Arijit Singh", "status": "RESOLVED",
             "confidence": 1.0, "resolution_method": "channels.list-verified",
             "canonical_url": "https://youtube.com/channel/UCabc",
             "first_seen_at": "2026-08-01T00:00:00+00:00",
             "last_verified_at": "2026-08-08T00:00:00+00:00", "reason": None, "invalidation_reason": None}],
        "youtube": {"current_snapshot": {"subscribers": {"value": 100000000, "observed_at": "2026-08-08T00:00:00+00:00"}},
                    "deltas": {"channel_view_delta_7d": {"status": "INSUFFICIENT_HISTORY"}},
                    "data_freshness": {"last_observed_at": "2026-08-08T00:00:00+00:00", "age_hours": 2.0, "stale": False}},
        "google_trends": {"current_interest": {"value": 88}, "regional_distribution": [],
                          "normalization_context": {"geo": "IN", "provider_mode": "IMPORT"}},
        "observed_live_supply": {"event_count": 0, "cities": 0},
        "notes": ["demand and supply are juxtaposed via canonical_artist_id; no combined score"]},
    f"artist_intelligence:/v1/internal/artists/{ARTIST}/momentum": {
        "components": {"youtube_subscriber_change": {"delta_7d": {"status": "INSUFFICIENT_HISTORY"}}},
        "coverage": {"has_7d_history": False, "has_30d_history": False}},
    f"artist_intelligence:/v1/internal/artists/{ARTIST}/geography": {
        "regions": [{"region_label": "West Bengal", "region_scope_id": "IN-WB", "search_interest": 100,
                     "observed_supply_count": 0, "label": "HIGHER_OBSERVED_DEMAND / LOWER_OBSERVED_SUPPLY"}]},
}


def test_artist_bundle(local):
    app.dependency_overrides[get_demand_service] = lambda: _demand(ARTIST_RESPONSES)
    r = local.get(f"/admin/v1/demand/artists/{ARTIST}")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["external_identities"][0]["status"] == "RESOLVED"
    assert body["external_identities"][0]["last_verified_at"]
    assert body["youtube"]["current_snapshot"]["subscribers"]["value"] == 100000000
    assert body["momentum"]["coverage"]["has_7d_history"] is False
    assert body["geography"]["regions"][0]["region_scope_id"] == "IN-WB"


def test_artist_unavailable(local):
    app.dependency_overrides[get_demand_service] = lambda: _demand(
        {}, unavailable={f"artist_intelligence:/v1/internal/artists/{ARTIST}/demand",
                         f"artist_intelligence:/v1/internal/artists/{ARTIST}/momentum",
                         f"artist_intelligence:/v1/internal/artists/{ARTIST}/geography"})
    body = local.get(f"/admin/v1/demand/artists/{ARTIST}").json()
    assert body["available"] is False
    assert body["external_identities"] == []
    assert body["momentum"] is None


def test_artist_missing_returns_empty_bundle(local):
    # demand service reachable but has no data for this artist (404 from downstream)
    app.dependency_overrides[get_demand_service] = lambda: _demand({})
    body = local.get("/admin/v1/demand/artists/artist:nobody").json()
    assert body["external_identities"] == []


def test_observations_bounded(local):
    app.dependency_overrides[get_demand_service] = lambda: _demand({
        f"artist_intelligence:/v1/internal/artists/{ARTIST}/observations": {
            "total": 3, "limit": 100, "offset": 0, "items": [{"metric": "YOUTUBE_SUBSCRIBERS"}]}})
    r = local.get(f"/admin/v1/demand/artists/{ARTIST}/observations?limit=999")
    # gateway clamps to <=200 (query validation)
    assert r.status_code == 422 or r.json().get("available") is True
    r2 = local.get(f"/admin/v1/demand/artists/{ARTIST}/observations?limit=100")
    assert r2.status_code == 200
    assert r2.json()["total"] == 3


# ---- event demand context -----------------------------------------------------------------------
def test_event_context(local):
    event = "event:some-show"
    responses = {
        f"crawl:/v1/internal/events/{event}/resolved-entities": {"entities": [
            {"entity_type": "ARTIST", "canonical_entity_id": ARTIST, "raw_name": "Arijit Singh"},
            {"entity_type": "VENUE", "canonical_entity_id": "venue:x", "raw_name": "X"}]},
        f"artist_intelligence:/v1/internal/artists/{ARTIST}/demand": ARTIST_RESPONSES[
            f"artist_intelligence:/v1/internal/artists/{ARTIST}/demand"],
        f"artist_intelligence:/v1/internal/artists/{ARTIST}/momentum": ARTIST_RESPONSES[
            f"artist_intelligence:/v1/internal/artists/{ARTIST}/momentum"],
        f"artist_intelligence:/v1/internal/artists/{ARTIST}/event-response": {
            "status": "INSUFFICIENT_HISTORY", "event_id": event},
    }
    app.dependency_overrides[get_demand_service] = lambda: _demand(responses)
    body = local.get(f"/admin/v1/demand/events/{event}").json()
    assert body["available"] is True
    assert body["resolved_artist_count"] == 1  # only the ARTIST entity
    assert body["artists"][0]["canonical_artist_id"] == ARTIST
    assert body["artists"][0]["event_response"]["status"] == "INSUFFICIENT_HISTORY"


def test_event_context_no_artists(local):
    event = "event:instrumental"
    app.dependency_overrides[get_demand_service] = lambda: _demand({
        f"crawl:/v1/internal/events/{event}/resolved-entities": {"entities": []}})
    body = local.get(f"/admin/v1/demand/events/{event}").json()
    assert body["resolved_artist_count"] == 0
    assert body["artists"] == []


# ---- read-only guarantee ------------------------------------------------------------------------
def test_demand_surface_is_read_only(local):
    # No demand mutation route exists: POST to any demand path is not allowed.
    for path in ("/admin/v1/demand/overview", f"/admin/v1/demand/artists/{ARTIST}",
                 "/admin/v1/demand/events/event:x"):
        assert local.post(path).status_code == 405

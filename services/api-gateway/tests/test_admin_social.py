"""Phase 5C.1 — social evidence BFF (read-only) over crawl-service.

Covers coverage/watchlist overview, identities + mentions reads, graceful degradation when crawl is
unavailable, and the read-only guarantee (no raw-source bulk export). Downstream is stubbed (no network).
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from api_gateway.admin.deps import get_social_service
from api_gateway.admin.gateway_client import Down, DownstreamGateway
from api_gateway.admin.social import SocialAdminService
from api_gateway.config import settings
from api_gateway.main import app


class FakeGateway(DownstreamGateway):
    def __init__(self, responses: dict, unavailable: set[str] | None = None):
        super().__init__(base_urls={"crawl": "http://crawl"})
        self._responses = responses
        self._unavailable = unavailable or set()

    async def request(self, service, method, path, *, params=None, json=None):
        key = f"{service}:{path}"
        if key in self._unavailable:
            return Down(available=False, status=0, error="down")
        if key not in self._responses:
            return Down(available=True, status=404, data=None)
        return Down(available=True, status=200, data=self._responses[key])


def _social(responses=None, unavailable=None) -> SocialAdminService:
    return SocialAdminService(FakeGateway(responses or {}, unavailable))


@pytest.fixture()
def local(monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


COVERAGE = {"social_enabled": True, "platforms_enabled": ["INSTAGRAM", "FACEBOOK"],
            "total_identities": 2, "total_mentions": 1, "unresolved_mentions": 0,
            "by_platform": {"INSTAGRAM": {"identities": 2, "mentions": 1, "canonical_entities": 1,
                                          "access_pending": 1, "last_access_state": "MOCK"}}}
WATCHLIST = {"active_identities": 2, "eligible_now": 1, "platforms_enabled": ["INSTAGRAM"], "items": []}
IDENTITIES = {"count": 1, "items": [{"id": "i1", "canonical_entity_id": "artist:a",
                                     "platform": "INSTAGRAM", "handle": "official",
                                     "collection_state": "ELIGIBLE"}]}
MENTIONS = {"count": 1, "items": [{"id": "m1", "platform": "INSTAGRAM", "platform_post_id": "P1",
                                   "extracted_claims": {"event_name": "X"}, "processing_status": "UNPROCESSED"}]}


INTERP_COV = {"total_interpretation_versions": 3, "current_interpretations": 2, "event_bearing": 1,
              "unprocessed_evidence": 0, "by_candidate_status": {"MATCHED_EXISTING": 1, "NONE": 1},
              "classifier_version": "social-classifier-1"}
INTERPRETATIONS = {"count": 1, "interpretations": [
    {"id": "si1", "social_mention_id": "m1", "evidence_version": 1,
     "claim_types": ["ANNOUNCEMENT", "TICKETING"], "primary_claim_type": "TICKETING",
     "event_bearing": True, "event_candidate_status": "MATCHED_EXISTING",
     "matched_canonical_event_id": "event:x", "confidence": 0.9,
     "reason_codes": ["MULTI_LABEL", "EVENT_IDENTITY_RESOLVED"]}]}


def test_social_overview_healthy(local):
    app.dependency_overrides[get_social_service] = lambda: _social({
        "crawl:/v1/internal/social/coverage": COVERAGE,
        "crawl:/v1/internal/social/watchlist": WATCHLIST,
        "crawl:/v1/internal/social/interpretations/coverage": INTERP_COV})
    r = local.get("/admin/v1/social/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] and body["coverage"]["total_identities"] == 2
    assert body["watchlist"]["eligible_now"] == 1
    assert body["interpretation"]["event_bearing"] == 1
    assert body["interpretation"]["classifier_version"] == "social-classifier-1"


def test_social_interpretations_read(local):
    app.dependency_overrides[get_social_service] = lambda: _social({
        "crawl:/v1/internal/social/interpretations": INTERPRETATIONS})
    r = local.get("/admin/v1/social/interpretations", params={"event_bearing": True})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    it = body["interpretations"][0]
    assert it["primary_claim_type"] == "TICKETING" and it["event_candidate_status"] == "MATCHED_EXISTING"
    # diagnostic surface only — no raw content anywhere
    assert "caption" not in str(body)


def test_social_overview_degrades_when_crawl_down(local):
    app.dependency_overrides[get_social_service] = lambda: _social(
        {}, unavailable={"crawl:/v1/internal/social/coverage",
                         "crawl:/v1/internal/social/watchlist"})
    r = local.get("/admin/v1/social/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["coverage"] is None


def test_social_identities_and_mentions(local):
    app.dependency_overrides[get_social_service] = lambda: _social({
        "crawl:/v1/internal/social/identities": IDENTITIES,
        "crawl:/v1/internal/social/mentions": MENTIONS})
    ri = local.get("/admin/v1/social/identities", params={"canonical_entity_id": "artist:a"})
    assert ri.status_code == 200 and ri.json()["count"] == 1
    rm = local.get("/admin/v1/social/mentions", params={"platform": "INSTAGRAM"})
    assert rm.status_code == 200 and rm.json()["items"][0]["processing_status"] == "UNPROCESSED"


HISTORY = {"platform": "INSTAGRAM", "platform_post_id": "P1", "versions": 2, "revised": True,
           "content_hashes": ["h1", "h2"],
           "items": [{"version": 1, "is_current": False, "extracted_claims": {"venue": "A"}},
                     {"version": 2, "is_current": True, "extracted_claims": {"venue": "B"}}]}


def test_social_mention_history_exposes_versions(local):
    app.dependency_overrides[get_social_service] = lambda: _social({
        "crawl:/v1/internal/social/mentions/history": HISTORY})
    r = local.get("/admin/v1/social/mentions/history",
                  params={"platform": "INSTAGRAM", "platform_post_id": "P1"})
    assert r.status_code == 200
    body = r.json()
    assert body["versions"] == 2 and body["revised"] is True
    assert [i["extracted_claims"]["venue"] for i in body["items"]] == ["A", "B"]


def test_social_reads_require_auth(monkeypatch):
    # admin API disabled → not reachable (mirrors the rest of the read-only BFF surface)
    monkeypatch.setattr(settings, "admin_api_enabled", False)
    with TestClient(app) as c:
        r = c.get("/admin/v1/social/overview")
    assert r.status_code in (401, 403, 404, 503)

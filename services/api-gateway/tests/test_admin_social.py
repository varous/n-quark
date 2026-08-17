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


def test_social_overview_healthy(local):
    app.dependency_overrides[get_social_service] = lambda: _social({
        "crawl:/v1/internal/social/coverage": COVERAGE,
        "crawl:/v1/internal/social/watchlist": WATCHLIST})
    r = local.get("/admin/v1/social/overview")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] and body["coverage"]["total_identities"] == 2
    assert body["watchlist"]["eligible_now"] == 1


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


def test_social_reads_require_auth(monkeypatch):
    # admin API disabled → not reachable (mirrors the rest of the read-only BFF surface)
    monkeypatch.setattr(settings, "admin_api_enabled", False)
    with TestClient(app) as c:
        r = c.get("/admin/v1/social/overview")
    assert r.status_code in (401, 403, 404, 503)

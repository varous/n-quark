"""Phase 5B.2.4 — data-quality review + governed correction BFF."""
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from api_gateway.admin import auth
from api_gateway.admin.deps import get_admin_service
from api_gateway.admin.gateway_client import Down, DownstreamGateway
from api_gateway.admin.service import AdminService
from api_gateway.config import settings
from api_gateway.main import app

CORRECT = "crawl:/v1/internal/entity-resolution/correct"
REVIEW = "crawl:/v1/internal/entity-resolution/review-queue"


class FakeGateway(DownstreamGateway):
    def __init__(self, responses):
        super().__init__(base_urls={"crawl": "http://crawl", "artist_intelligence": "http://ai"})
        self._responses = responses
        self.sent = []

    async def request(self, service, method, path, *, params=None, json=None):
        self.sent.append({"path": path, "json": json})
        key = f"{service}:{path}"
        if key not in self._responses:
            return Down(available=True, status=404, data={"detail": "nf"})
        return Down(available=True, status=200, data=self._responses[key])


def _svc(responses=None):
    return AdminService(FakeGateway(responses or {}))


@pytest.fixture()
def oidc(monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", False)
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_client_id", "c.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "oidc_allowed_domain", "clockwork-av.com")
    monkeypatch.setattr(settings, "admin_research_config_enabled", True)
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_correction_requires_auth(oidc):
    assert oidc.post("/admin/v1/data-quality/correct",
                     json={"action": "MARK_PLACEHOLDER", "canonical_entity_id": "venue:x"}).status_code == 401


def test_correction_forwards_operator_as_actor(oidc):
    fake = FakeGateway({CORRECT: {"canonical_entity_id": "venue:x", "candidates_quarantined": 2}})
    app.dependency_overrides[get_admin_service] = lambda: AdminService(fake)
    oidc.cookies.set(settings.session_cookie_name, auth.issue_session("op@clockwork-av.com", "VIEWER", auth_mode="oidc"))
    r = oidc.post("/admin/v1/data-quality/correct",
                  json={"action": "MARK_PLACEHOLDER", "canonical_entity_id": "venue:x", "reason": "tba"})
    assert r.status_code == 200
    assert fake.sent[-1]["json"]["actor"] == "op@clockwork-av.com"   # operator forwarded, never client-supplied


def test_correction_rejects_unknown_action(oidc):
    app.dependency_overrides[get_admin_service] = lambda: _svc({})
    oidc.cookies.set(settings.session_cookie_name, auth.issue_session("op@clockwork-av.com", "VIEWER", auth_mode="oidc"))
    assert oidc.post("/admin/v1/data-quality/correct", json={"action": "DELETE_EVERYTHING"}).status_code == 422


def test_correction_blocked_when_flag_disabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    monkeypatch.setattr(settings, "admin_research_config_enabled", False)
    app.dependency_overrides[get_admin_service] = lambda: _svc({})
    with TestClient(app) as c:
        assert c.post("/admin/v1/data-quality/correct",
                      json={"action": "MARK_PLACEHOLDER", "canonical_entity_id": "v:x"}).status_code == 503
    app.dependency_overrides.clear()


def test_review_queue_read(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    app.dependency_overrides[get_admin_service] = lambda: _svc({REVIEW: {"count": 1, "items": [{"candidate_id": "c1"}]}})
    with TestClient(app) as c:
        body = c.get("/admin/v1/data-quality/review-queue").json()
    assert body["available"] is True and body["count"] == 1
    app.dependency_overrides.clear()


def test_metrics_read(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    app.dependency_overrides[get_admin_service] = lambda: _svc({
        "crawl:/v1/internal/entity-resolution/quality-metrics": {
            "mentions_processed": 520, "flow": {"placeholder_suppressed": 2, "compound_split": 5},
            "open_review_items": 3, "interpretation_method": "deterministic"}})
    with TestClient(app) as c:
        body = c.get("/admin/v1/data-quality/metrics").json()
    assert body["available"] is True and body["mentions_processed"] == 520
    assert body["interpretation_method"] == "deterministic"
    app.dependency_overrides.clear()


def test_event_detail_includes_interpreted(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    ev = "event:x"
    app.dependency_overrides[get_admin_service] = lambda: _svc({
        f"graph:/v1/graph/nodes/{ev}": {"properties": {"display_name": "Show"}},
        f"crawl:/v1/internal/events/{ev}/interpreted-entities": {
            "venue": {"state": "NOT_ANNOUNCED", "canonical_entity_id": None, "raw_mentions": ["Venue to be announced"]},
            "artists": {"resolved": [], "resolved_count": 0, "needs_review": [], "needs_review_count": 0, "unresolved_mentions": []},
            "organizer": {"state": "NOT_PROVIDED", "canonical_entity_id": None, "raw_mentions": []}}})
    with TestClient(app) as c:
        body = c.get(f"/admin/v1/events/{ev}").json()
    assert body["interpreted"]["venue"]["state"] == "NOT_ANNOUNCED"
    assert body["interpreted"]["venue"]["canonical_entity_id"] is None
    app.dependency_overrides.clear()

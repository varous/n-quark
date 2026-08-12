"""Phase 5B.1 — research-watchlist BFF (the one narrow authenticated WRITE surface).

Covers: authenticated operator required; the research-config flag gates writes (503 when off); the
authenticated identity is forwarded as ``created_by``; reads degrade gracefully; and the canonical /
observation / graph mutation surfaces remain blocked from here. Downstream is stubbed (no network).
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from api_gateway.admin import auth
from api_gateway.admin.deps import get_watchlist_service
from api_gateway.admin.gateway_client import Down, DownstreamGateway
from api_gateway.admin.watchlist import WatchlistAdminService
from api_gateway.config import settings
from api_gateway.main import app

BASE = "/v1/internal/watchlist"


class FakeGateway(DownstreamGateway):
    """Records the last forwarded request so tests can assert created_by propagation."""

    def __init__(self, responses: dict, unavailable: set[str] | None = None):
        super().__init__(base_urls={"crawl": "http://crawl", "artist_intelligence": "http://ai"})
        self._responses = responses
        self._unavailable = unavailable or set()
        self.sent: list[dict] = []

    async def request(self, service, method, path, *, params=None, json=None):
        self.sent.append({"service": service, "method": method, "path": path,
                          "params": params, "json": json})
        key = f"{service}:{path}"
        if key in self._unavailable:
            return Down(available=False, status=0, error="down")
        if key not in self._responses:
            return Down(available=True, status=404, data={"detail": "not found"})
        return Down(available=True, status=200, data=self._responses[key])


def _svc(responses=None, unavailable=None) -> WatchlistAdminService:
    return WatchlistAdminService(FakeGateway(responses or {}, unavailable))


@pytest.fixture()
def local(monkeypatch) -> Generator[TestClient, None, None]:
    """Local single-context console with research configuration ENABLED."""
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    monkeypatch.setattr(settings, "admin_research_config_enabled", True)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def oidc(monkeypatch) -> Generator[TestClient, None, None]:
    """Production auth mode (OIDC), research config enabled — used to prove auth + created_by."""
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", False)
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "oidc_allowed_domain", "clockwork-av.com")
    monkeypatch.setattr(settings, "admin_research_config_enabled", True)
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


ADD_RESPONSE = {"created": True, "target": {"id": "wt_1", "display_name": "Anuv Jain",
                                            "status": "RESOLUTION_PENDING"}}


# ---- authenticated operator required ------------------------------------------------------------
def test_writes_require_authentication(oidc):
    # no session cookie → 401 on both read and write
    assert oidc.get("/admin/v1/research/watchlist").status_code == 401
    assert oidc.post("/admin/v1/research/watchlist",
                     json={"display_name": "Anuv Jain"}).status_code == 401


def test_created_by_is_the_authenticated_operator(oidc):
    fake = FakeGateway({f"artist_intelligence:{BASE}": ADD_RESPONSE})
    app.dependency_overrides[get_watchlist_service] = lambda: WatchlistAdminService(fake)
    token = auth.issue_session("sourav@clockwork-av.com", "VIEWER", auth_mode="oidc")
    oidc.cookies.set(settings.session_cookie_name, token)
    r = oidc.post("/admin/v1/research/watchlist", json={"display_name": "Anuv Jain"})
    assert r.status_code == 200
    # the gateway attaches the authenticated identity — the operator never supplies it
    body = fake.sent[-1]["json"]
    assert body["created_by"] == "sourav@clockwork-av.com"
    assert body["display_name"] == "Anuv Jain"


# ---- research-config flag gates writes ----------------------------------------------------------
def test_writes_blocked_when_research_config_disabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    monkeypatch.setattr(settings, "admin_research_config_enabled", False)
    app.dependency_overrides[get_watchlist_service] = lambda: _svc({f"artist_intelligence:{BASE}": ADD_RESPONSE})
    with TestClient(app) as c:
        # read still allowed
        c_list = c.get("/admin/v1/research/watchlist")
        assert c_list.status_code == 200
        # write refused with 503 (capability disabled)
        assert c.post("/admin/v1/research/watchlist",
                      json={"display_name": "X"}).status_code == 503
    app.dependency_overrides.clear()


# ---- happy-path writes + reads ------------------------------------------------------------------
def test_add_target(local):
    app.dependency_overrides[get_watchlist_service] = lambda: _svc({f"artist_intelligence:{BASE}": ADD_RESPONSE})
    r = local.post("/admin/v1/research/watchlist", json={"display_name": "Anuv Jain"})
    assert r.status_code == 200 and r.json()["target"]["id"] == "wt_1"


def test_add_requires_display_name(local):
    app.dependency_overrides[get_watchlist_service] = lambda: _svc({})
    assert local.post("/admin/v1/research/watchlist", json={}).status_code == 422


def test_list_and_diagnostics(local):
    app.dependency_overrides[get_watchlist_service] = lambda: _svc({
        f"artist_intelligence:{BASE}": {"total": 1, "targets": [{"id": "wt_1", "status": "WATCHING"}]},
        f"artist_intelligence:{BASE}/diagnostics": {"total": 1, "watching": 1, "resolution_pending": 0}})
    lst = local.get("/admin/v1/research/watchlist")
    assert lst.status_code == 200 and lst.json()["available"] is True and lst.json()["total"] == 1
    diag = local.get("/admin/v1/research/watchlist/diagnostics")
    assert diag.json()["watching"] == 1


def test_pause_resume_priority(local):
    app.dependency_overrides[get_watchlist_service] = lambda: _svc({
        f"artist_intelligence:{BASE}/wt_1/pause": {"id": "wt_1", "status": "PAUSED"},
        f"artist_intelligence:{BASE}/wt_1/resume": {"id": "wt_1", "status": "WATCHING"},
        f"artist_intelligence:{BASE}/wt_1/priority": {"id": "wt_1", "priority": 10}})
    assert local.post("/admin/v1/research/watchlist/wt_1/pause").json()["status"] == "PAUSED"
    assert local.post("/admin/v1/research/watchlist/wt_1/resume").json()["status"] == "WATCHING"
    assert local.post("/admin/v1/research/watchlist/wt_1/priority",
                      json={"priority": 10}).json()["priority"] == 10


def test_canonical_integrity_read(local):
    app.dependency_overrides[get_watchlist_service] = lambda: _svc({
        f"artist_intelligence:{BASE}/canonical-integrity": {
            "registry_available": True, "registry_canonical_artists": 64,
            "watch_targets": {"referenced": 1, "orphans": []},
            "candidates": {"referenced": 1, "orphans": []},
            "external_identities": {"referenced": 1, "orphans": []},
            "demand_observations": {"referenced": 1, "orphans": []},
            "orphan_total": 0}})
    r = local.get("/admin/v1/research/watchlist/canonical-integrity")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True and body["orphan_total"] == 0
    assert body["watch_targets"]["orphans"] == []


def test_reads_degrade_when_demand_down(local):
    app.dependency_overrides[get_watchlist_service] = lambda: _svc(
        {}, unavailable={f"artist_intelligence:{BASE}"})
    body = local.get("/admin/v1/research/watchlist").json()
    assert body["available"] is False and body["targets"] == []


# ---- canonical / observation / graph state stays protected from this surface --------------------
def test_canonical_mutations_still_blocked(local):
    # research config being on must NOT open the canonical/admin mutation surfaces.
    assert local.post("/admin/v1/operations/capture-now",
                      json={"source": "x", "source_record_id": "y"}).status_code == 503
    # no watchlist route can mutate observations/graph/entities — those verbs simply do not exist here.
    for path in ("/admin/v1/entities", "/admin/v1/graph/subgraph", "/admin/v1/events"):
        assert local.post(path).status_code == 405

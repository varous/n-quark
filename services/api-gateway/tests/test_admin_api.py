"""Admin BFF API tests: auth gating, role enforcement, pagination/filter passthrough, graceful
downstream failure, bounded graph, audited operations. Downstreams are stubbed (no network)."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from api_gateway.admin import auth
from api_gateway.admin.deps import get_admin_service, get_audit_store
from api_gateway.admin.gateway_client import Down, DownstreamGateway
from api_gateway.admin.service import AdminService
from api_gateway.config import settings
from api_gateway.main import app


class FakeAudit:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)
        return {"id": "aud1", "request_id": kw["request_id"]}

    def list(self, *, limit=50, offset=0, **_filters):
        return {"count": len(self.records), "items": self.records}


class FakeGateway(DownstreamGateway):
    """Deterministic downstream: returns canned payloads or an unavailable marker per path."""

    def __init__(self, responses: dict, unavailable: set[str] | None = None):
        super().__init__(base_urls={"crawl": "http://crawl", "graph": "http://graph"})
        self._responses = responses
        self._unavailable = unavailable or set()
        self.calls: list = []

    async def request(self, service, method, path, *, params=None, json=None):
        self.calls.append((service, method, path, params))
        key = f"{service}:{path}"
        if key in self._unavailable:
            return Down(available=False, status=0, error="down")
        return Down(available=True, status=200, data=self._responses.get(key, {}))


def _svc(responses=None, unavailable=None) -> AdminService:
    return AdminService(FakeGateway(responses or {}, unavailable))


@pytest.fixture()
def client(monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_dev_auth_enabled", True)
    audit = FakeAudit()
    app.dependency_overrides[get_audit_store] = lambda: audit
    with TestClient(app) as c:
        c.audit = audit  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _token(role="VIEWER"):
    return auth.issue_dev_token("tester", role)


def _hdr(role="VIEWER"):
    return {"Authorization": f"Bearer {_token(role)}"}


# ---- gating + auth ------------------------------------------------------------------------------
def test_admin_disabled_returns_404(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_enabled", False)
    c = TestClient(app)
    assert c.get("/admin/v1/dashboard").status_code == 404


def test_unauthenticated_rejected(client):
    assert client.get("/admin/v1/dashboard").status_code == 401


def test_dev_login_and_me(client):
    r = client.post("/admin/v1/auth/login", json={"username": "alice", "role": "OPERATOR"})
    assert r.status_code == 200
    tok = r.json()["token"]
    me = client.get("/admin/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.status_code == 200 and me.json()["role"] == "OPERATOR"


def test_login_rejects_bad_role(client):
    assert client.post("/admin/v1/auth/login",
                       json={"username": "x", "role": "SUPERUSER"}).status_code == 422


def test_login_disabled_when_dev_auth_off(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_dev_auth_enabled", False)
    assert client.post("/admin/v1/auth/login", json={"username": "x"}).status_code == 403


# ---- read models --------------------------------------------------------------------------------
def test_dashboard_ok(client):
    app.dependency_overrides[get_admin_service] = lambda: _svc({
        "crawl:/v1/internal/capture-schedule": {"events": [
            {"source": "boshow", "tracking_status": "ACTIVE", "distinct_state_count": 3,
             "transition_count": 2, "capture_count": 5, "canonical_event_id": "event:a", "city": "Kolkata"}]},
        "crawl:/v1/internal/entity-resolution/coverage": {"by_entity_type": {
            "ARTIST": {"resolution_rate": 0.9, "ambiguous_mentions": 1, "cross_source_canonical_entities": 0}}},
        "crawl:/v1/internal/capture-schedule/jobs": {"jobs": []},
    })
    r = client.get("/admin/v1/dashboard", headers=_hdr())
    assert r.status_code == 200
    assert r.json()["cards"]["active_tracked_events"] == 1


def test_events_pagination_passthrough(client):
    fake = _svc({"crawl:/v1/internal/capture-schedule": {"events": [
        {"canonical_event_id": f"event:{i}", "source": "boshow", "source_record_id": str(i),
         "distinct_state_count": i, "transition_count": 0} for i in range(10)]}})
    app.dependency_overrides[get_admin_service] = lambda: fake
    r = client.get("/admin/v1/events?limit=3&offset=0", headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 10 and len(body["events"]) == 3 and body["limit"] == 3


def test_downstream_unavailable_handled(client):
    app.dependency_overrides[get_admin_service] = lambda: _svc(
        {}, unavailable={"crawl:/v1/internal/capture-schedule"})
    r = client.get("/admin/v1/events", headers=_hdr())
    assert r.status_code == 200 and r.json()["available"] is False and r.json()["events"] == []


def test_entities_filter_passthrough(client):
    fake = _svc({"crawl:/v1/internal/entity-resolution/entities":
                 {"count": 1, "entities": [{"canonical_entity_id": "artist:x", "entity_type": "ARTIST"}]}})
    app.dependency_overrides[get_admin_service] = lambda: fake
    r = client.get("/admin/v1/entities?entity_type=ARTIST&cross_source_only=true", headers=_hdr())
    assert r.status_code == 200 and r.json()["count"] == 1


# ---- role enforcement + operations --------------------------------------------------------------
def test_viewer_cannot_operate(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_operational_actions_enabled", True)
    app.dependency_overrides[get_admin_service] = lambda: _svc()
    r = client.post("/admin/v1/operations/rerun-enrichment",
                    json={"event_id": "event:a"}, headers=_hdr("VIEWER"))
    assert r.status_code == 403


def test_analyst_cannot_operate(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_operational_actions_enabled", True)
    app.dependency_overrides[get_admin_service] = lambda: _svc()
    r = client.post("/admin/v1/operations/rerun-enrichment",
                    json={"event_id": "event:a"}, headers=_hdr("ANALYST"))
    assert r.status_code == 403


def test_operations_disabled_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_operational_actions_enabled", False)
    app.dependency_overrides[get_admin_service] = lambda: _svc()
    r = client.post("/admin/v1/operations/rerun-enrichment",
                    json={"event_id": "event:a"}, headers=_hdr("OPERATOR"))
    assert r.status_code == 503


def test_operator_action_audited(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_operational_actions_enabled", True)
    app.dependency_overrides[get_admin_service] = lambda: _svc(
        {"crawl:/v1/internal/events/event:a/enrichment/resolve": {"outcome": "ENRICHMENT_SUCCEEDED"}})
    r = client.post("/admin/v1/operations/rerun-enrichment",
                    json={"event_id": "event:a", "reason": "manual recheck"}, headers=_hdr("OPERATOR"))
    assert r.status_code == 200 and "request_id" in r.json()
    assert client.audit.records and client.audit.records[0]["action"] == "RERUN_ENRICHMENT"


def test_operation_requires_target(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_operational_actions_enabled", True)
    app.dependency_overrides[get_admin_service] = lambda: _svc()
    r = client.post("/admin/v1/operations/rerun-enrichment", json={}, headers=_hdr("OPERATOR"))
    assert r.status_code == 422


def test_audit_requires_admin(client):
    app.dependency_overrides[get_admin_service] = lambda: _svc()
    assert client.get("/admin/v1/audit", headers=_hdr("OPERATOR")).status_code == 403
    assert client.get("/admin/v1/audit", headers=_hdr("ADMIN")).status_code == 200


# ---- bounded graph (service unit) ---------------------------------------------------------------
async def test_subgraph_respects_node_cap(monkeypatch):
    monkeypatch.setattr(settings, "admin_graph_max_nodes", 5)
    monkeypatch.setattr(settings, "admin_graph_max_depth", 3)

    def node_resp(nid):
        return {"id": nid, "type": "event", "properties": {"display_name": nid}}

    class Fanout(DownstreamGateway):
        def __init__(self):
            super().__init__(base_urls={"graph": "http://g"})

        async def request(self, service, method, path, *, params=None, json=None):
            if path.endswith("/neighbors"):
                nid = path.split("/")[-2]
                return Down(True, 200, {"neighbors": [
                    {"relationship": "FEATURES", "direction": "out",
                     "node": {"id": f"{nid}-{i}", "properties": {}}} for i in range(10)]})
            nid = path.split("/")[-1]
            return Down(True, 200, node_resp(nid))

    svc = AdminService(Fanout())
    out = await svc.subgraph("root", depth=3)
    assert out["node_count"] <= 5 and out["capped"] is True

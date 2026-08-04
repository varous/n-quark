"""Admin Phase B governance commands: RBAC, reason-required, idempotency, conflict propagation,
append-only decisions + audit, reversal (+ dependency block), and capture-now role gating.
Crawl's governance surface is stubbed; the decision/audit stores use isolated SQLite."""

import tempfile
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api_gateway.admin import auth
from api_gateway.admin.audit import AuditStore
from api_gateway.admin.decisions import DecisionStore
from api_gateway.admin.deps import get_admin_service, get_audit_store, get_decision_store
from api_gateway.admin.gateway_client import Down, DownstreamGateway
from api_gateway.admin.service import AdminService
from api_gateway.config import settings
from api_gateway.db.models import Base
from api_gateway.main import app


class StubGateway(DownstreamGateway):
    def __init__(self, responses: dict):
        super().__init__(base_urls={"crawl": "http://crawl", "graph": "http://graph"})
        self._responses = responses
        self.calls: list = []

    async def request(self, service, method, path, *, params=None, json=None):
        self.calls.append((path, json))
        r = self._responses.get(path, Down(True, 200, {}))
        return r


def _fresh_stores():
    tmp = tempfile.mkdtemp()
    engine = create_engine(f"sqlite:///{tmp}/gw.db", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, expire_on_commit=False)
    return AuditStore(sf), DecisionStore(sf)


@pytest.fixture()
def client(monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_dev_auth_enabled", True)
    monkeypatch.setattr(settings, "admin_operational_actions_enabled", True)
    audit, decisions = _fresh_stores()
    app.dependency_overrides[get_audit_store] = lambda: audit
    app.dependency_overrides[get_decision_store] = lambda: decisions
    with TestClient(app) as c:
        c.audit, c.decisions = audit, decisions  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


def _use_gateway(responses):
    app.dependency_overrides[get_admin_service] = lambda: AdminService(StubGateway(responses))


def _hdr(role):
    return {"Authorization": f"Bearer {auth.issue_dev_token('tester', role)}"}


_ACCEPT_OK = {"/v1/internal/governance/accept": Down(True, 200, {
    "candidate": {"id": "c1", "status": "RESOLVED"}, "previous_status": "AMBIGUOUS",
    "previous_canonical_entity_id": None})}


# ---- RBAC ---------------------------------------------------------------------------------------
def test_viewer_cannot_accept(client):
    _use_gateway(_ACCEPT_OK)
    r = client.post("/admin/v1/resolution-decisions/accept",
                    json={"candidate_id": "c1", "canonical_entity_id": "artist:pilu"}, headers=_hdr("VIEWER"))
    assert r.status_code == 403


def test_analyst_can_accept_and_records_decision(client):
    _use_gateway(_ACCEPT_OK)
    r = client.post("/admin/v1/resolution-decisions/accept",
                    json={"candidate_id": "c1", "canonical_entity_id": "artist:pilu"}, headers=_hdr("ANALYST"))
    assert r.status_code == 200
    body = r.json()
    assert body["decision"]["action"] == "ACCEPT_CANDIDATE" and body["decision"]["new_status"] == "RESOLVED"
    # audit + decision persisted
    assert client.decisions.list()["count"] == 1


def test_analyst_cannot_supersede(client):
    _use_gateway({"/v1/internal/governance/supersede-legacy": Down(True, 200, {"relationship": "SUPERSEDED_BY"})})
    r = client.post("/admin/v1/resolution-decisions/supersede-legacy",
                    json={"entity_type": "VENUE", "legacy_entity_id": "venue:a",
                          "canonical_entity_id": "venue:b", "reason": "dup"}, headers=_hdr("ANALYST"))
    assert r.status_code == 403


def test_admin_can_supersede(client):
    _use_gateway({"/v1/internal/governance/supersede-legacy": Down(True, 200, {"relationship": "SUPERSEDED_BY"})})
    r = client.post("/admin/v1/resolution-decisions/supersede-legacy",
                    json={"entity_type": "VENUE", "legacy_entity_id": "venue:a",
                          "canonical_entity_id": "venue:b", "reason": "clear duplicate"}, headers=_hdr("ADMIN"))
    assert r.status_code == 200


# ---- reason required ----------------------------------------------------------------------------
def test_create_entity_requires_reason(client):
    _use_gateway({"/v1/internal/governance/create-entity": Down(True, 200, {"candidate": {"status": "RESOLVED"}})})
    r = client.post("/admin/v1/resolution-decisions/create-entity",
                    json={"entity_type": "ARTIST", "canonical_name": "X", "candidate_id": "c1"},
                    headers=_hdr("ANALYST"))
    assert r.status_code == 422


# ---- idempotency --------------------------------------------------------------------------------
def test_idempotent_double_submit(client):
    _use_gateway(_ACCEPT_OK)
    body = {"candidate_id": "c1", "canonical_entity_id": "artist:pilu", "idempotency_key": "k1"}
    r1 = client.post("/admin/v1/resolution-decisions/accept", json=body, headers=_hdr("ANALYST"))
    r2 = client.post("/admin/v1/resolution-decisions/accept", json=body, headers=_hdr("ANALYST"))
    assert r1.status_code == 200 and r2.status_code == 200
    assert r2.json()["already_applied"] is True
    assert client.decisions.list()["count"] == 1  # only one decision recorded


# ---- conflict propagation -----------------------------------------------------------------------
def test_handle_conflict_propagates_409(client):
    _use_gateway({"/v1/internal/governance/accept":
                  Down(True, 409, {"detail": {"code": "HANDLE_ALREADY_LINKED"}})})
    r = client.post("/admin/v1/resolution-decisions/accept",
                    json={"candidate_id": "c1", "canonical_entity_id": "artist:pilu"}, headers=_hdr("ANALYST"))
    assert r.status_code == 409


def test_stale_preview_propagates_409(client):
    _use_gateway({"/v1/internal/governance/accept":
                  Down(True, 409, {"detail": {"code": "STALE_PREVIEW"}})})
    r = client.post("/admin/v1/resolution-decisions/accept",
                    json={"candidate_id": "c1", "canonical_entity_id": "artist:pilu",
                          "expected_status": "UNRESOLVED"}, headers=_hdr("ANALYST"))
    assert r.status_code == 409


# ---- reversal -----------------------------------------------------------------------------------
def test_reverse_requires_admin_and_reason(client):
    _use_gateway(_ACCEPT_OK)
    dec = client.post("/admin/v1/resolution-decisions/accept",
                      json={"candidate_id": "c1", "canonical_entity_id": "artist:pilu"},
                      headers=_hdr("ANALYST")).json()["decision"]
    # analyst cannot reverse
    assert client.post(f"/admin/v1/resolution-decisions/{dec['id']}/reverse",
                       json={"reason": "x"}, headers=_hdr("ANALYST")).status_code == 403
    # admin without reason -> 422
    assert client.post(f"/admin/v1/resolution-decisions/{dec['id']}/reverse",
                       json={}, headers=_hdr("ADMIN")).status_code == 422


def test_reverse_accept_flow(client):
    app.dependency_overrides[get_admin_service] = lambda: AdminService(StubGateway({
        **_ACCEPT_OK,
        "/v1/internal/governance/reverse-accept": Down(True, 200, {"candidate": {"status": "AMBIGUOUS"}})}))
    dec = client.post("/admin/v1/resolution-decisions/accept",
                      json={"candidate_id": "c1", "canonical_entity_id": "artist:pilu"},
                      headers=_hdr("ANALYST")).json()["decision"]
    rev = client.post(f"/admin/v1/resolution-decisions/{dec['id']}/reverse",
                      json={"reason": "wrong call"}, headers=_hdr("ADMIN"))
    assert rev.status_code == 200 and rev.json()["reversed"] == dec["id"]
    assert client.decisions.get(dec["id"])["reversed"] is True


# ---- capture-now --------------------------------------------------------------------------------
def test_capture_now_role_gate(client):
    _use_gateway({"/v1/internal/capture-schedule/capture-now": Down(True, 200, {"claimed": True})})
    assert client.post("/admin/v1/operations/capture-now",
                       json={"source": "boshow", "source_record_id": "x"},
                       headers=_hdr("ANALYST")).status_code == 403
    r = client.post("/admin/v1/operations/capture-now",
                    json={"source": "boshow", "source_record_id": "x"}, headers=_hdr("OPERATOR"))
    assert r.status_code == 200 and "request_id" in r.json()


def test_operations_disabled_blocks_commands(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_operational_actions_enabled", False)
    _use_gateway(_ACCEPT_OK)
    r = client.post("/admin/v1/resolution-decisions/accept",
                    json={"candidate_id": "c1", "canonical_entity_id": "artist:pilu"}, headers=_hdr("ANALYST"))
    assert r.status_code == 503

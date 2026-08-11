"""Admin Phase C — local-only inspection hardening.

Covers: single INTERNAL_USER local context (no login, no roles), event search/filtering,
source diagnostics, richer system-health, bounded filtered CSV/JSON export, the resolution
diagnostic queues, and the local-only deployment boundary. Downstreams are stubbed (no network).
"""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api_gateway.admin.deps import get_admin_service, get_audit_store
from api_gateway.admin.gateway_client import Down, DownstreamGateway
from api_gateway.admin.service import AdminService
from api_gateway.config import settings
from api_gateway.main import app

REPO_ROOT = Path(__file__).resolve().parents[3]


class FakeAudit:
    def __init__(self):
        self.records = []

    def record(self, **kw):
        self.records.append(kw)
        return {"id": "aud1", "request_id": kw["request_id"]}

    def list(self, *, limit=50, offset=0, **_filters):
        return {"count": len(self.records), "items": self.records}


class FakeGateway(DownstreamGateway):
    def __init__(self, responses: dict, unavailable: set[str] | None = None):
        super().__init__(base_urls={"crawl": "http://crawl", "graph": "http://graph"})
        self._responses = responses
        self._unavailable = unavailable or set()

    async def request(self, service, method, path, *, params=None, json=None):
        key = f"{service}:{path}"
        if key in self._unavailable:
            return Down(available=False, status=0, error="down")
        return Down(available=True, status=200, data=self._responses.get(key, {}))


def _svc(responses=None, unavailable=None) -> AdminService:
    return AdminService(FakeGateway(responses or {}, unavailable))


@pytest.fixture()
def local(monkeypatch) -> Generator[TestClient, None, None]:
    """Gateway in local mode: admin enabled, no auth, single INTERNAL_USER context."""
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    audit = FakeAudit()
    app.dependency_overrides[get_audit_store] = lambda: audit
    with TestClient(app) as c:
        c.audit = audit  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.clear()


# ---- single local internal-user context (no auth) -----------------------------------------------
def test_local_mode_opens_without_login(local):
    # no Authorization header at all
    app.dependency_overrides[get_admin_service] = lambda: _svc(
        {"crawl:/v1/internal/capture-schedule": {"events": []},
         "crawl:/v1/internal/entity-resolution/coverage": {"by_entity_type": {}},
         "crawl:/v1/internal/capture-schedule/jobs": {"jobs": []}})
    assert local.get("/admin/v1/dashboard").status_code == 200


def test_local_mode_me_reports_internal_user(local):
    me = local.get("/admin/v1/auth/me").json()
    assert me["role"] == "INTERNAL_USER"
    assert me["auth_mode"] == "local"
    assert me["local_mode"] is True


def test_local_mode_no_role_barrier_on_audit(local):
    # audit is ADMIN-only under RBAC; in local mode the single context reaches it with no token
    app.dependency_overrides[get_admin_service] = lambda: _svc()
    assert local.get("/admin/v1/audit").status_code == 200


def test_admin_still_404_when_api_disabled(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_enabled", False)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    assert TestClient(app).get("/admin/v1/dashboard").status_code == 404


# ---- event search + filters ---------------------------------------------------------------------
def _events_responses():
    events = [
        {"canonical_event_id": "event:a", "source": "boshow", "source_record_id": "sr-a",
         "city": "Kolkata", "last_capture_status": "SUCCESS", "distinct_state_count": 2,
         "transition_count": 1, "capture_gap_hours": 1},
        {"canonical_event_id": "event:b", "source": "district", "source_record_id": "sr-b",
         "city": "Mumbai", "last_capture_status": "SUCCESS", "distinct_state_count": 1,
         "transition_count": 0, "capture_gap_hours": 1},
    ]
    return {
        "crawl:/v1/internal/capture-schedule": {"events": events},
        "graph:/v1/graph/nodes/event:a": {"type": "event",
            "properties": {"display_name": "Skinny Mos Live", "city": "Kolkata", "starts_at": "2026-09-01"}},
        "graph:/v1/graph/nodes/event:b": {"type": "event",
            "properties": {"display_name": "F1 Sim Night", "city": "Mumbai", "starts_at": "2026-10-01"}},
        "crawl:/v1/internal/events/event:a/resolved-entities": {"entities": [
            {"entity_type": "ARTIST", "raw_name": "Skinny Mos", "status": "RESOLVED"}]},
        "crawl:/v1/internal/events/event:b/resolved-entities": {"entities": [
            {"entity_type": "ARTIST", "raw_name": "Pilu", "status": "AMBIGUOUS"}]},
    }


def test_event_search_by_title(local):
    app.dependency_overrides[get_admin_service] = lambda: _svc(_events_responses())
    r = local.get("/admin/v1/events?q=skinny").json()
    assert r["hydrated"] is True
    assert [e["canonical_event_id"] for e in r["events"]] == ["event:a"]


def test_event_filter_by_city_and_resolution_status(local):
    app.dependency_overrides[get_admin_service] = lambda: _svc(_events_responses())
    r = local.get("/admin/v1/events?resolution_status=AMBIGUOUS").json()
    assert [e["canonical_event_id"] for e in r["events"]] == ["event:b"]
    r2 = local.get("/admin/v1/events?city=kolkata").json()
    assert [e["canonical_event_id"] for e in r2["events"]] == ["event:a"]


def test_event_filter_capture_state_is_cheap(local):
    app.dependency_overrides[get_admin_service] = lambda: _svc(_events_responses())
    r = local.get("/admin/v1/events?capture_state=SUCCESS").json()
    assert r["count"] == 2 and r["hydrated"] is False  # no text/date/resolution filter → shallow


# ---- source diagnostics -------------------------------------------------------------------------
def test_source_diagnostics(local):
    app.dependency_overrides[get_admin_service] = lambda: _svc({
        "crawl:/v1/internal/capture-schedule": {"events": [
            {"source": "district", "city": "Mutiple Cities, India", "capture_gap_hours": 2,
             "distinct_state_count": 2, "transition_count": 1, "last_success_at": "2026-08-01T00:00:00Z"},
            {"source": "district", "city": "Mumbai", "capture_gap_hours": 100,
             "distinct_state_count": 1, "transition_count": 0, "last_success_at": "2026-07-01T00:00:00Z"}]},
        "crawl:/v1/internal/capture-schedule/jobs": {"jobs": [
            {"status": "SUCCEEDED"}, {"status": "FAILED_TERMINAL", "last_error_code": "PARSE_ERROR"}]},
        "crawl:/v1/internal/entity-resolution/coverage": {"by_entity_type": {
            "ARTIST": {"resolved_mentions": 3, "ambiguous_mentions": 1, "unresolved_mentions": 0}}},
    })
    d = local.get("/admin/v1/sources/district/diagnostics").json()
    assert d["tracked_events"] == 2
    assert d["capture_success_rate"] == 0.5
    assert d["failure_classifications"] == {"PARSE_ERROR": 1}
    assert d["parser_failures"] == 1
    assert d["geography"]["placeholder"] == 1  # "Mutiple Cities, India" flagged present-but-invalid
    assert d["geography"]["valid"] == 1
    assert d["stale_events"] == 1
    assert d["events_with_transitions"] == 1


# ---- richer system health -----------------------------------------------------------------------
def test_system_health_has_flags_and_dq(local, monkeypatch):
    app.dependency_overrides[get_admin_service] = lambda: _svc({
        "crawl:/v1/internal/capture-schedule": {"events": [
            {"canonical_event_id": "event:a", "city": None, "capture_gap_hours": 100}]},
        "crawl:/v1/internal/entity-resolution/coverage": {"by_entity_type": {
            "ARTIST": {"ambiguous_mentions": 2}}},
        "crawl:/v1/internal/capture-schedule/jobs": {"jobs": [{"status": "FAILED_TERMINAL"}]},
        "crawl:/v1/internal/entity-resolution/entities": {"entities": []},
        "graph:/v1/graph/nodes/event:a": {"type": "event", "properties": {}},
    })
    h = local.get("/admin/v1/system-health").json()
    assert "feature_flags" in h and h["feature_flags"]["admin_local_mode"] is True
    assert h["data_quality"]["events_missing_geography"] == 1
    assert h["data_quality"]["stale_tracked_events"] == 1
    assert h["data_quality"]["failed_capture_jobs"] == 1
    assert h["data_quality"]["ambiguous_artists"] == 2
    assert "checked_at" in h
    # per-service last_check present
    assert all("last_check" in s for s in h["services"].values())


# ---- resolution diagnostic queues ---------------------------------------------------------------
def test_resolution_queue_status_filter(local):
    app.dependency_overrides[get_admin_service] = lambda: _svc({
        "crawl:/v1/internal/entity-resolution/unresolved": {"items": [
            {"raw_name": "Pilu", "status": "AMBIGUOUS"},
            {"raw_name": "Town Hall", "status": "UNRESOLVED"},
            {"raw_name": "X", "status": "POSSIBLE_MATCH"}]}})
    full = local.get("/admin/v1/resolution-queue").json()
    assert full["by_status"] == {"AMBIGUOUS": 1, "UNRESOLVED": 1, "POSSIBLE_MATCH": 1}
    assert set(full["states"]) >= {"AMBIGUOUS", "UNRESOLVED", "POSSIBLE_MATCH", "CONFLICT", "LOW_CONFIDENCE"}
    only = local.get("/admin/v1/resolution-queue?status=AMBIGUOUS").json()
    assert only["count"] == 1 and only["items"][0]["raw_name"] == "Pilu"


# ---- bounded filtered export --------------------------------------------------------------------
def test_export_events_csv_respects_filter(local):
    app.dependency_overrides[get_admin_service] = lambda: _svc(_events_responses())
    r = local.get("/admin/v1/export/events?format=csv&q=skinny")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    body = r.text.strip().splitlines()
    assert len(body) == 2  # header + one filtered row
    assert "event:a" in body[1] and "event:b" not in r.text


def test_export_events_json(local):
    app.dependency_overrides[get_admin_service] = lambda: _svc(_events_responses())
    r = local.get("/admin/v1/export/events?format=json")
    assert r.status_code == 200
    body = r.json()
    assert body["table"] == "events" and body["count"] == 2


def test_export_unknown_table_404(local):
    app.dependency_overrides[get_admin_service] = lambda: _svc()
    assert local.get("/admin/v1/export/nonsense?format=csv").status_code == 404


def test_export_bad_format_422(local):
    app.dependency_overrides[get_admin_service] = lambda: _svc()
    assert local.get("/admin/v1/export/events?format=xml").status_code == 422


# ---- deployment boundary ------------------------------------------------------------------------
# Admin D introduces ONE deliberate exception: the authenticated public console
# (deploy/fly/admin-console.toml). Everything else stays as before. The invariant that MUST hold
# everywhere is that ADMIN_LOCAL_MODE (the unauthenticated single-context bypass) is NEVER enabled on
# any cloud manifest — that would expose the console with no login.

def _directive_lines(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("#"))


def test_local_mode_never_enabled_on_any_cloud_manifest():
    """The unauthenticated local-mode bypass must never be on in any Fly manifest (service or deploy/)."""
    manifests = list(REPO_ROOT.glob("services/*/fly.toml")) + list(REPO_ROOT.glob("deploy/fly/*.toml"))
    assert manifests, "expected fly manifests"
    for toml in manifests:
        directives = _directive_lines(toml.read_text())
        assert 'ADMIN_LOCAL_MODE = "true"' not in directives, f"{toml} enables local mode on cloud"


def test_private_service_manifests_do_not_enable_admin():
    """The private service manifests + the crawl-space api-gateway never serve the admin console."""
    for toml in REPO_ROOT.glob("services/*/fly.toml"):
        directives = _directive_lines(toml.read_text())
        assert 'ADMIN_API_ENABLED = "true"' not in directives
        assert "frontend" not in directives.lower()
    # the frontend has no cloud (Fly) app of its own — it is baked into the nquark-admin image
    assert not (REPO_ROOT / "frontend" / "fly.toml").exists()


def test_gateway_fly_manifest_pins_admin_off():
    text = (REPO_ROOT / "services" / "api-gateway" / "fly.toml").read_text()
    # the crawl-space public API gateway keeps the admin surface pinned off
    assert 'NQUARK_ADMIN_API_ENABLED = "false"' in text
    assert 'NQUARK_ADMIN_LOCAL_MODE = "false"' in text


def test_admin_console_manifest_is_authenticated_and_read_only():
    """The one public console manifest must be authenticated (OIDC on, local-mode off) and read-only."""
    toml = REPO_ROOT / "deploy" / "fly" / "admin-console.toml"
    assert toml.exists(), "expected deploy/fly/admin-console.toml"
    directives = _directive_lines(toml.read_text())
    assert 'NQUARK_ADMIN_API_ENABLED = "true"' in directives
    assert 'NQUARK_OIDC_ENABLED = "true"' in directives
    assert 'NQUARK_ADMIN_LOCAL_MODE = "false"' in directives
    assert 'NQUARK_ADMIN_OPERATIONAL_ACTIONS_ENABLED = "false"' in directives
    # secrets must NOT be inlined in the manifest
    assert "NQUARK_OIDC_CLIENT_SECRET =" not in directives
    assert "NQUARK_ADMIN_SESSION_SECRET =" not in directives

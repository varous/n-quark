"""Admin D.1 — hard read-only enforcement (server-side, independent of the frontend).

The production console mints only the read-only VIEWER role and runs with operational actions disabled.
These tests prove that writes are refused server-side: reads reject non-GET methods, the operational
endpoints return 503 when disabled, and the governed mutation endpoints require a higher role than a
VIEWER holds (403). Auth login/logout/callback remain usable.
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from api_gateway.admin import auth
from api_gateway.config import settings
from api_gateway.main import app


@pytest.fixture()
def viewer(monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", False)
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_client_id", "c.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "oidc_client_secret", "s")
    monkeypatch.setattr(settings, "admin_operational_actions_enabled", False)  # read-only
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    c = TestClient(app)
    c.cookies.set(settings.session_cookie_name, auth.issue_session("sourav@clockwork-av.com", "VIEWER", auth_mode="oidc"))
    yield c


READ_PATHS = ["/admin/v1/dashboard", "/admin/v1/events", "/admin/v1/entities",
              "/admin/v1/system-health", "/admin/v1/sources", "/admin/v1/demand/overview"]


def test_read_paths_reject_write_methods(viewer):
    for path in READ_PATHS:
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            r = viewer.request(method, path, json={})
            # Never a 2xx: the path has only a GET route, so a write method is 405 (or 404/403).
            assert r.status_code in (403, 404, 405), f"{method} {path} -> {r.status_code} (must not mutate)"


def test_operational_endpoints_blocked_for_readonly_console(viewer):
    # The console mints VIEWER and disables operational actions — either gate rejects the write:
    # the role gate (require_operator -> 403 for a VIEWER) or the flag gate (503 when disabled).
    for path in ("/admin/v1/operations/capture-now", "/admin/v1/operations/rerun-enrichment",
                 "/admin/v1/operations/rerun-entity-resolution"):
        r = viewer.post(path, json={"source": "boshow", "source_record_id": "x", "event_id": "e", "reason": "r"})
        assert r.status_code in (403, 503), f"{path} -> {r.status_code} (must be rejected)"


def test_governed_mutations_forbidden_for_viewer(viewer):
    for verb in ("accept", "reject", "create-entity", "link-handle", "mark-alias", "correct-series"):
        r = viewer.post(f"/admin/v1/resolution-decisions/{verb}", json={"reason": "r"})
        assert r.status_code == 403, f"{verb} -> {r.status_code} (VIEWER must be forbidden)"


def test_reads_still_work_and_auth_endpoints_exempt(viewer):
    assert viewer.get("/admin/v1/auth/me").status_code == 200
    assert viewer.get("/admin/v1/auth/status").status_code == 200
    assert viewer.post("/admin/v1/auth/logout").status_code == 204

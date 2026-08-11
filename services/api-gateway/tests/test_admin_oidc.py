"""Admin D — Google Workspace OIDC + session-cookie auth on the public console.

Covers the allowlist decision, the signed anti-CSRF state, the unauthenticated /auth/status entry,
the login redirect, session-cookie authentication through require_role, and the callback wiring
(exchange stubbed — no network)."""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from api_gateway.admin import auth, oidc
from api_gateway.config import settings
from api_gateway.main import app


@pytest.fixture()
def oidc_client(monkeypatch) -> Generator[TestClient, None, None]:
    """Console in production auth mode: admin API on, local mode OFF, OIDC configured."""
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", False)
    monkeypatch.setattr(settings, "oidc_enabled", True)
    monkeypatch.setattr(settings, "oidc_client_id", "test-client.apps.googleusercontent.com")
    monkeypatch.setattr(settings, "oidc_client_secret", "test-secret")
    monkeypatch.setattr(settings, "oidc_allowed_domain", "clockwork-av.com")
    monkeypatch.setattr(settings, "oidc_allowed_emails", "")
    monkeypatch.setattr(settings, "public_base_url", "https://nquark-admin.fly.dev")
    monkeypatch.setattr(settings, "session_cookie_secure", False)  # TestClient uses http
    with TestClient(app) as c:
        yield c


# ---- allowlist ----------------------------------------------------------------------------------
def test_allowlist_domain_and_hd(monkeypatch):
    monkeypatch.setattr(settings, "oidc_allowed_domain", "clockwork-av.com")
    monkeypatch.setattr(settings, "oidc_allowed_emails", "")
    assert auth.email_is_allowed("sourav@clockwork-av.com", "clockwork-av.com") is True
    assert auth.email_is_allowed("sourav@clockwork-av.com", None) is True          # email suffix
    assert auth.email_is_allowed("someone@gmail.com", None) is False
    assert auth.email_is_allowed("someone@gmail.com", "clockwork-av.com") is False  # hd forged? email wins


def test_allowlist_extra_emails(monkeypatch):
    monkeypatch.setattr(settings, "oidc_allowed_domain", "clockwork-av.com")
    monkeypatch.setattr(settings, "oidc_allowed_emails", "guest@partner.com, other@x.com")
    assert auth.email_is_allowed("guest@partner.com", None) is True
    assert auth.email_is_allowed("nope@partner.com", None) is False


def test_allowlist_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "oidc_allowed_domain", None)
    monkeypatch.setattr(settings, "oidc_allowed_emails", "")
    assert auth.email_is_allowed("anyone@anywhere.com", "anywhere.com") is False


# ---- signed state -------------------------------------------------------------------------------
def test_state_roundtrip_and_tamper(monkeypatch):
    monkeypatch.setattr(settings, "admin_session_secret", "unit-secret")
    st = oidc.issue_state("/events?source=boshow", nonce="nnn")
    nxt, nonce = oidc.verify_state(st)
    assert nxt == "/events?source=boshow"
    assert nonce == "nnn"
    with pytest.raises(oidc.OidcError):
        oidc.verify_state(st + "x")
    with pytest.raises(oidc.OidcError):
        oidc.verify_state(None)


def test_state_rejects_open_redirect(monkeypatch):
    monkeypatch.setattr(settings, "admin_session_secret", "unit-secret")
    assert oidc.verify_state(oidc.issue_state("//evil.com"))[0] == "/"
    assert oidc.verify_state(oidc.issue_state("https://evil.com"))[0] == "/"


# ---- entry + login ------------------------------------------------------------------------------
def test_auth_status_reports_oidc_unauthenticated(oidc_client):
    r = oidc_client.get("/admin/v1/auth/status")
    assert r.status_code == 200
    body = r.json()
    assert body["auth_mode"] == "oidc"
    assert body["authenticated"] is False
    assert body["login_url"] == "/admin/v1/auth/login"


def test_login_redirects_to_google(oidc_client):
    r = oidc_client.get("/admin/v1/auth/login", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "test-client.apps.googleusercontent.com" in loc
    assert "nquark-admin.fly.dev%2Fadmin%2Fv1%2Fauth%2Fcallback" in loc
    assert "hd=clockwork-av.com" in loc


def test_me_requires_auth_without_cookie(oidc_client):
    assert oidc_client.get("/admin/v1/auth/me").status_code == 401


def test_session_cookie_authenticates(oidc_client):
    token = auth.issue_session("sourav@clockwork-av.com", "VIEWER", auth_mode="oidc")
    oidc_client.cookies.set(settings.session_cookie_name, token)
    r = oidc_client.get("/admin/v1/auth/me")
    assert r.status_code == 200
    body = r.json()
    assert body["sub"] == "sourav@clockwork-av.com"
    assert body["auth_mode"] == "oidc"
    assert body["mutations_enabled"] is False


def test_callback_sets_cookie_and_redirects(oidc_client, monkeypatch):
    async def fake_exchange(code: str, expected_nonce: str):
        assert code == "auth-code-123"
        assert expected_nonce == "the-nonce"  # the callback must pass the state's nonce through
        return oidc.OidcResult(email="sourav@clockwork-av.com", hosted_domain="clockwork-av.com")

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    state = oidc.issue_state("/overview", nonce="the-nonce")
    r = oidc_client.get(f"/admin/v1/auth/callback?code=auth-code-123&state={state}",
                        follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/overview"
    assert settings.session_cookie_name in r.cookies


def test_callback_error_redirects_to_login_error(oidc_client):
    r = oidc_client.get("/admin/v1/auth/callback?error=access_denied", follow_redirects=False)
    assert r.status_code == 302
    assert "login_error" in r.headers["location"]


def test_session_cookie_is_httponly_secure_samesite(oidc_client, monkeypatch):
    # In production session_cookie_secure=true; the cookie must be HttpOnly + Secure + SameSite=Lax.
    monkeypatch.setattr(settings, "session_cookie_secure", True)

    async def fake_exchange(code: str, expected_nonce: str):
        return oidc.OidcResult(email="sourav@clockwork-av.com", hosted_domain="clockwork-av.com")

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    state = oidc.issue_state("/", nonce="n1")
    r = oidc_client.get(f"/admin/v1/auth/callback?code=c&state={state}", follow_redirects=False)
    setc = r.headers.get("set-cookie", "")
    assert settings.session_cookie_name + "=" in setc
    assert "HttpOnly" in setc
    assert "Secure" in setc
    assert "samesite=lax" in setc.lower()
    assert "Path=/" in setc


def test_expired_state_rejected(monkeypatch):
    monkeypatch.setattr(settings, "admin_session_secret", "unit-secret")
    import json
    import time as _t
    old = int(_t.time()) - (oidc._STATE_TTL_SECONDS + 60)
    body = oidc._b64u(json.dumps({"n": "/", "ts": old, "nc": "x"}, separators=(",", ":")).encode())
    stale = f"{body}.{oidc._sign(body.encode())}"  # correctly signed, but issued too long ago
    with pytest.raises(oidc.OidcError):
        oidc.verify_state(stale)


def test_logout_clears_cookie(oidc_client):
    token = auth.issue_session("sourav@clockwork-av.com", "VIEWER", auth_mode="oidc")
    oidc_client.cookies.set(settings.session_cookie_name, token)
    r = oidc_client.post("/admin/v1/auth/logout")
    assert r.status_code == 204


# ---- /v1/platform/status is authenticated (Admin D.2) -------------------------------------------
# The aggregate status enumerates internal service names + health (production topology). It must not
# be publicly readable: same VIEWER principal as the rest of the console. Downstream fan-out is stubbed
# to empty so these stay hermetic (no network, no localhost dependency).
@pytest.fixture()
def no_downstream(monkeypatch):
    monkeypatch.setattr(type(settings), "downstream_services", property(lambda self: {}), raising=False)


def test_platform_status_requires_auth(oidc_client, no_downstream):
    r = oidc_client.get("/v1/platform/status")
    assert r.status_code == 401  # production/OIDC, no cookie -> unauthorized, no topology leaked
    assert "services" not in r.json()


def test_platform_status_authenticated_viewer(oidc_client, no_downstream):
    token = auth.issue_session("sourav@clockwork-av.com", "VIEWER", auth_mode="oidc")
    oidc_client.cookies.set(settings.session_cookie_name, token)
    r = oidc_client.get("/v1/platform/status")
    assert r.status_code == 200
    assert "services" in r.json()


def test_platform_status_hidden_when_admin_api_disabled(monkeypatch, no_downstream):
    # A deployment with the admin surface off exposes no topology at all (404, not a public 200).
    monkeypatch.setattr(settings, "admin_api_enabled", False)
    with TestClient(app) as c:
        assert c.get("/v1/platform/status").status_code == 404


def test_platform_status_local_mode_open(monkeypatch, no_downstream):
    # Local single-context console: internal user, no login, reads succeed.
    monkeypatch.setattr(settings, "admin_api_enabled", True)
    monkeypatch.setattr(settings, "admin_local_mode", True)
    with TestClient(app) as c:
        assert c.get("/v1/platform/status").status_code == 200

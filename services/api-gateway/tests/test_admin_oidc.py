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
    st = oidc.issue_state("/events?source=boshow")
    assert oidc.verify_state(st) == "/events?source=boshow"
    with pytest.raises(oidc.OidcError):
        oidc.verify_state(st + "x")
    with pytest.raises(oidc.OidcError):
        oidc.verify_state(None)


def test_state_rejects_open_redirect(monkeypatch):
    monkeypatch.setattr(settings, "admin_session_secret", "unit-secret")
    assert oidc.verify_state(oidc.issue_state("//evil.com")) == "/"
    assert oidc.verify_state(oidc.issue_state("https://evil.com")) == "/"


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
    async def fake_exchange(code: str):
        assert code == "auth-code-123"
        return oidc.OidcResult(email="sourav@clockwork-av.com", hosted_domain="clockwork-av.com")

    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    state = oidc.issue_state("/overview")
    r = oidc_client.get(f"/admin/v1/auth/callback?code=auth-code-123&state={state}",
                        follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/overview"
    assert settings.session_cookie_name in r.cookies


def test_callback_error_redirects_to_login_error(oidc_client):
    r = oidc_client.get("/admin/v1/auth/callback?error=access_denied", follow_redirects=False)
    assert r.status_code == 302
    assert "login_error" in r.headers["location"]


def test_logout_clears_cookie(oidc_client):
    token = auth.issue_session("sourav@clockwork-av.com", "VIEWER", auth_mode="oidc")
    oidc_client.cookies.set(settings.session_cookie_name, token)
    r = oidc_client.post("/admin/v1/auth/logout")
    assert r.status_code == 204

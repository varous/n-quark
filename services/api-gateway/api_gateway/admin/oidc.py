"""Google Workspace OIDC for the authenticated production console (Admin D).

Standard OAuth 2.0 **authorization-code** flow:

    /admin/v1/auth/login     -> 302 to Google's consent screen (with a signed anti-CSRF `state`)
    /admin/v1/auth/callback  -> exchange the code for tokens (server->server, TLS), validate the
                                id_token claims, check the Workspace domain allowlist, mint a signed
                                httpOnly session cookie, then 302 back to the app root.

The id_token is fetched directly from Google's token endpoint over TLS in a server-to-server request
(never from the browser), so its claims are decoded and validated (iss / aud / exp / email_verified /
hosted-domain) without re-verifying the RS256 signature — that avoids adding a JWT/crypto dependency
while keeping the token's provenance authenticated by the TLS channel to accounts.google.com. Only the
allowlist decision (auth.email_is_allowed) grants access.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import httpx

from api_gateway.admin import auth
from api_gateway.config import settings

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
CALLBACK_PATH = "/admin/v1/auth/callback"
_STATE_TTL_SECONDS = 600  # 10 minutes to complete the round-trip


class OidcError(Exception):
    """Raised on any configuration or verification failure; the caller maps it to an HTTP response."""


@dataclass(frozen=True)
class OidcResult:
    email: str
    hosted_domain: str | None


def is_configured() -> bool:
    return bool(settings.oidc_enabled and settings.oidc_client_id and settings.oidc_client_secret)


def redirect_uri() -> str:
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        raise OidcError("public_base_url is not set; cannot build the OIDC redirect URI")
    return f"{base}{CALLBACK_PATH}"


# ---- signed, stateless anti-CSRF state -----------------------------------------------------------
def _sign(payload: bytes) -> str:
    digest = hmac.new(settings.admin_session_secret.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_state(next_path: str = "/") -> str:
    body = _b64u(json.dumps({"n": next_path, "ts": int(time.time())},
                            separators=(",", ":")).encode())
    return f"{body}.{_sign(body.encode())}"


def verify_state(state: str | None) -> str:
    """Return the validated `next` path, or raise OidcError."""
    if not state:
        raise OidcError("missing state")
    try:
        body, sig = state.split(".", 1)
        if not hmac.compare_digest(sig, _sign(body.encode())):
            raise OidcError("bad state signature")
        claims = json.loads(_b64u_dec(body))
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise OidcError("malformed state") from exc
    if int(claims.get("ts", 0)) + _STATE_TTL_SECONDS < int(time.time()):
        raise OidcError("state expired")
    nxt = str(claims.get("n", "/"))
    # Only allow same-origin relative redirects (defense against open-redirect via `next`).
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"


def authorization_url(state: str) -> str:
    if not is_configured():
        raise OidcError("OIDC is not configured")
    from urllib.parse import urlencode
    params = {
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    if settings.oidc_allowed_domain:
        params["hd"] = settings.oidc_allowed_domain  # domain hint (not a security control on its own)
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


def _decode_id_token_claims(id_token: str) -> dict:
    try:
        _, payload_b64, _ = id_token.split(".")
        return json.loads(_b64u_dec(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise OidcError("could not decode id_token") from exc


def _validate_claims(claims: dict) -> OidcResult:
    if claims.get("iss") not in VALID_ISSUERS:
        raise OidcError("unexpected token issuer")
    if claims.get("aud") != settings.oidc_client_id:
        raise OidcError("token audience mismatch")
    if int(claims.get("exp", 0)) < int(time.time()):
        raise OidcError("id_token expired")
    if not claims.get("email_verified"):
        raise OidcError("email not verified by Google")
    email = str(claims.get("email", ""))
    hd = claims.get("hd")
    if not auth.email_is_allowed(email, hd):
        raise OidcError("account is not permitted to access this console")
    return OidcResult(email=email, hosted_domain=hd)


async def exchange_code(code: str) -> OidcResult:
    """Exchange the authorization code for tokens and return the validated identity."""
    if not is_configured():
        raise OidcError("OIDC is not configured")
    data = {
        "code": code,
        "client_id": settings.oidc_client_id,
        "client_secret": settings.oidc_client_secret,
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(GOOGLE_TOKEN_ENDPOINT, data=data)
    except httpx.HTTPError as exc:
        raise OidcError(f"token exchange transport error: {exc}") from exc
    if resp.status_code != 200:
        raise OidcError(f"token exchange failed ({resp.status_code})")
    id_token = resp.json().get("id_token")
    if not id_token:
        raise OidcError("token response missing id_token")
    return _validate_claims(_decode_id_token_claims(id_token))

"""Google Workspace OIDC for the authenticated production console (Admin D).

Standard OAuth 2.0 **authorization-code** flow:

    /admin/v1/auth/login     -> 302 to Google's consent screen (signed anti-CSRF `state` + `nonce`)
    /admin/v1/auth/callback  -> exchange the code for tokens (server->server, TLS), **cryptographically
                                verify** the id_token, check the Workspace domain allowlist, mint a
                                signed httpOnly session cookie, then 302 back to the app root.

Security posture (Admin D.1): the id_token is **not** trusted on the strength of the TLS channel alone.
Its RS256 signature is verified against Google's published JWKS (`GOOGLE_JWKS_URI`, cache-controlled),
and PyJWT enforces `aud` (our client id), `iss` (Google), and `exp`. On top of that we require
`email_verified`, the Workspace-domain allowlist (auth.email_is_allowed), and a `nonce` that must equal
the one bound into the signed login `state` (replay/CSRF binding). A forged token, a token signed by an
unknown key, or any claim mismatch is rejected.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

import httpx
import jwt
from jwt import PyJWKSet

from api_gateway.admin import auth
from api_gateway.config import settings

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URI = "https://www.googleapis.com/oauth2/v3/certs"
VALID_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
CALLBACK_PATH = "/admin/v1/auth/callback"
_STATE_TTL_SECONDS = 600  # 10 minutes to complete the round-trip
_LEEWAY_SECONDS = 30       # clock-skew tolerance for exp
_JWKS_MIN_TTL = 300        # cache Google's keys at least 5 min


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


# ---- signed, stateless anti-CSRF state (binds the login attempt to a nonce) ----------------------
def _sign(payload: bytes) -> str:
    digest = hmac.new(settings.admin_session_secret.encode(), payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_state(next_path: str = "/", *, nonce: str | None = None) -> str:
    """Sign {next, ts, nonce}. The nonce is echoed by Google into the id_token and re-checked on callback."""
    nonce = nonce or secrets.token_urlsafe(16)
    body = _b64u(json.dumps({"n": next_path, "ts": int(time.time()), "nc": nonce},
                            separators=(",", ":")).encode())
    return f"{body}.{_sign(body.encode())}"


def verify_state(state: str | None) -> tuple[str, str]:
    """Return `(next_path, nonce)` from a valid signed state, or raise OidcError."""
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
    nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"
    nonce = str(claims.get("nc", ""))
    if not nonce:
        raise OidcError("state missing nonce")
    return nxt, nonce


def build_login_url(next_path: str = "/") -> str:
    """Create a signed state (with a fresh nonce) and return the Google authorization URL."""
    if not is_configured():
        raise OidcError("OIDC is not configured")
    from urllib.parse import urlencode
    nonce = secrets.token_urlsafe(16)
    state = issue_state(next_path, nonce=nonce)
    params = {
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "access_type": "online",
        "prompt": "select_account",
    }
    if settings.oidc_allowed_domain:
        params["hd"] = settings.oidc_allowed_domain  # domain hint (not a security control on its own)
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


# ---- Google JWKS (cached) ------------------------------------------------------------------------
_jwks_cache: dict[str, object] = {"jwks": None, "exp": 0.0}


def _parse_max_age(cache_control: str | None) -> int:
    if not cache_control:
        return _JWKS_MIN_TTL
    for part in cache_control.split(","):
        part = part.strip()
        if part.startswith("max-age="):
            try:
                return max(_JWKS_MIN_TTL, int(part.split("=", 1)[1]))
            except ValueError:
                return _JWKS_MIN_TTL
    return _JWKS_MIN_TTL


async def _fetch_jwks() -> PyJWKSet:
    """Fetch Google's JWKS over TLS. Isolated so tests can inject keys without network."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(GOOGLE_JWKS_URI)
    resp.raise_for_status()
    ttl = _parse_max_age(resp.headers.get("cache-control"))
    _jwks_cache["exp"] = time.time() + ttl
    return PyJWKSet.from_dict(resp.json())


async def _get_jwks(force: bool = False) -> PyJWKSet:
    cached = _jwks_cache.get("jwks")
    if not force and cached is not None and time.time() < float(_jwks_cache["exp"]):
        return cached  # type: ignore[return-value]
    jwks = await _fetch_jwks()
    _jwks_cache["jwks"] = jwks
    return jwks


async def _signing_key(kid: str):
    """Find the public key for `kid`, refreshing the JWKS once if Google has rotated keys."""
    for force in (False, True):
        jwks = await _get_jwks(force=force)
        try:
            return jwks[kid].key
        except (KeyError, jwt.PyJWKError, jwt.exceptions.PyJWKSetError):
            if force:
                break
    raise OidcError("no matching Google signing key for token")


# ---- verification --------------------------------------------------------------------------------
async def verify_id_token(id_token: str, expected_nonce: str) -> OidcResult:
    """Cryptographically verify a Google id_token and apply the access policy. Raises OidcError."""
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.InvalidTokenError as exc:
        raise OidcError("malformed id_token header") from exc
    kid = header.get("kid")
    if not kid or header.get("alg") != "RS256":
        raise OidcError("id_token must be RS256 with a key id")
    key = await _signing_key(kid)
    try:
        claims = jwt.decode(
            id_token, key=key, algorithms=["RS256"],
            audience=settings.oidc_client_id,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
            leeway=_LEEWAY_SECONDS,
        )
    except jwt.ExpiredSignatureError as exc:
        raise OidcError("id_token expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise OidcError("token audience mismatch") from exc
    except jwt.InvalidTokenError as exc:  # bad signature / malformed / missing claim
        raise OidcError("id_token signature verification failed") from exc

    if claims.get("iss") not in VALID_ISSUERS:
        raise OidcError("unexpected token issuer")
    if expected_nonce and claims.get("nonce") != expected_nonce:
        raise OidcError("nonce mismatch")
    if not claims.get("email_verified"):
        raise OidcError("email not verified by Google")
    email = str(claims.get("email", ""))
    hd = claims.get("hd")
    if not auth.email_is_allowed(email, hd):
        raise OidcError("account is not permitted to access this console")
    return OidcResult(email=email, hosted_domain=hd)


async def _post_token(code: str) -> str:
    """Exchange the authorization code for tokens (server->server, TLS); return the raw id_token."""
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
    return id_token


async def exchange_code(code: str, expected_nonce: str) -> OidcResult:
    """Exchange the code, then cryptographically verify the returned id_token and apply policy."""
    if not is_configured():
        raise OidcError("OIDC is not configured")
    id_token = await _post_token(code)
    return await verify_id_token(id_token, expected_nonce)

"""Admin authentication & authorization.

Provider-neutral: `authenticate(token)` returns a `Principal` or None. The only implementation here is
an **isolated development** session (HMAC-signed token issued by a dev login), gated by
`ADMIN_DEV_AUTH_ENABLED` — it is never a production identity provider and there is no hard-coded
unrestricted admin. A later Google Workspace OIDC verifier implements the same `authenticate` contract.

Authorization is role-based and enforced **server-side** on every route (never via hidden UI).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from api_gateway.config import settings

# Ordered roles: a higher rank satisfies a lower requirement.
ROLES = ("VIEWER", "ANALYST", "OPERATOR", "ADMIN")
_RANK = {r: i for i, r in enumerate(ROLES)}

# Phase C: the single, unauthenticated local context. It satisfies every role requirement so the
# console needs no login and no role selector when the gateway runs in local mode.
INTERNAL_USER_ROLE = "INTERNAL_USER"


@dataclass(frozen=True)
class Principal:
    sub: str
    role: str
    auth_mode: str = "dev"

    def has_role(self, required: str) -> bool:
        if self.role == INTERNAL_USER_ROLE:
            return True  # local internal user has full inspection access
        return _RANK.get(self.role, -1) >= _RANK.get(required, 99)


def internal_user() -> Principal:
    """The fixed principal used in local mode (no token, single context)."""
    return Principal(sub="internal-user", role=INTERNAL_USER_ROLE, auth_mode="local")


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(payload: bytes) -> str:
    return _b64u(hmac.new(settings.admin_session_secret.encode(), payload, hashlib.sha256).digest())


def issue_dev_token(sub: str, role: str, *, ttl_seconds: int | None = None) -> str:
    if role not in _RANK:
        raise ValueError(f"unknown role {role!r}")
    exp = int(time.time()) + (ttl_seconds or settings.admin_session_ttl_seconds)
    body = _b64u(json.dumps({"sub": sub, "role": role, "exp": exp}, separators=(",", ":")).encode())
    return f"{body}.{_sign(body.encode())}"


def authenticate(token: str | None) -> Principal | None:
    """Verify a session token. Returns a Principal or None (never raises)."""
    if not token:
        return None
    try:
        body, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(body.encode())):
            return None
        claims = json.loads(_b64u_dec(body))
        if int(claims.get("exp", 0)) < int(time.time()):
            return None
        role = claims.get("role")
        if role not in _RANK:
            return None
        return Principal(sub=str(claims.get("sub", "")), role=role)
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def _extract_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


def require_role(required: str):
    """FastAPI dependency factory enforcing `required` role (server-side)."""

    def _dep(authorization: str | None = Header(default=None)) -> Principal:
        if not settings.admin_api_enabled:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="admin api disabled")
        if settings.admin_local_mode:
            return internal_user()  # single unauthenticated local context; no token, all roles
        principal = authenticate(_extract_token(authorization))
        if principal is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="authentication required",
                                headers={"WWW-Authenticate": "Bearer"})
        if not principal.has_role(required):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail=f"role {required} required (have {principal.role})")
        return principal

    return _dep


require_viewer = require_role("VIEWER")
require_analyst = require_role("ANALYST")
require_operator = require_role("OPERATOR")
require_admin = require_role("ADMIN")

"""Admin D.1 — cryptographic verification of Google id_tokens (no network).

A real RSA keypair is generated; its public key is served as an injected JWKS; tokens are signed with
the private key. Proves signature/issuer/audience/expiry/email_verified/domain/nonce are all enforced,
and that a valid Workspace identity is accepted. If any of these regress, these tests fail closed.
"""

import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKSet
from jwt.algorithms import RSAAlgorithm

from api_gateway.admin import oidc
from api_gateway.config import settings

CLIENT_ID = "test-client.apps.googleusercontent.com"
KID = "test-kid-1"


def _keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks_for(priv, kid=KID) -> PyJWKSet:
    jwk = json.loads(RSAAlgorithm.to_jwk(priv.public_key()))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return PyJWKSet.from_dict({"keys": [jwk]})


def _claims(**over):
    now = int(time.time())
    base = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "1234567890",
        "iat": now, "exp": now + 3600, "email": "sourav@clockwork-av.com",
        "email_verified": True, "hd": "clockwork-av.com", "nonce": "N",
    }
    base.update(over)
    return base


def _sign(priv, claims, *, kid=KID, alg="RS256") -> str:
    return jwt.encode(claims, priv, algorithm=alg, headers={"kid": kid})


@pytest.fixture()
def signer(monkeypatch):
    """Inject a fresh RSA keypair as Google's JWKS; configure the console's audience + domain."""
    priv = _keypair()
    jwks = _jwks_for(priv)

    async def fake_get_jwks(force: bool = False):
        return jwks

    monkeypatch.setattr(oidc, "_get_jwks", fake_get_jwks)
    monkeypatch.setattr(settings, "oidc_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "oidc_allowed_domain", "clockwork-av.com")
    monkeypatch.setattr(settings, "oidc_allowed_emails", "")
    return priv


# ---- the one that must pass ----------------------------------------------------------------------
async def test_valid_workspace_identity_accepted(signer):
    token = _sign(signer, _claims())
    result = await oidc.verify_id_token(token, "N")
    assert result.email == "sourav@clockwork-av.com"
    assert result.hosted_domain == "clockwork-av.com"


# ---- signature / key ----------------------------------------------------------------------------
async def test_forged_signature_rejected(signer):
    token = _sign(signer, _claims())
    forged = token[:-6] + ("aaaaaa" if not token.endswith("aaaaaa") else "bbbbbb")
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(forged, "N")


async def test_token_signed_with_unknown_key_rejected(signer):
    # Signed by a DIFFERENT private key but presented under the JWKS's kid → signature cannot verify.
    attacker = _keypair()
    token = _sign(attacker, _claims())
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(token, "N")


async def test_unknown_kid_rejected(signer):
    token = _sign(signer, _claims(), kid="not-in-jwks")
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(token, "N")


async def test_alg_none_rejected(signer):
    # An unsigned "alg=none" token must never be accepted.
    unsigned = jwt.encode(_claims(), key=None, algorithm="none", headers={"kid": KID})
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(unsigned, "N")


# ---- claim checks -------------------------------------------------------------------------------
async def test_wrong_issuer_rejected(signer):
    token = _sign(signer, _claims(iss="https://evil.example.com"))
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(token, "N")


async def test_wrong_audience_rejected(signer):
    token = _sign(signer, _claims(aud="some-other-client.apps.googleusercontent.com"))
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(token, "N")


async def test_expired_token_rejected(signer):
    now = int(time.time())
    token = _sign(signer, _claims(iat=now - 4000, exp=now - 3600))
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(token, "N")


async def test_unverified_email_rejected(signer):
    token = _sign(signer, _claims(email_verified=False))
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(token, "N")


async def test_wrong_workspace_domain_rejected(signer):
    token = _sign(signer, _claims(email="intruder@gmail.com", hd=None))
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(token, "N")


async def test_hd_spoof_with_foreign_email_rejected(signer):
    # A gmail account cannot pass by carrying a forged hd claim (email domain is authoritative).
    token = _sign(signer, _claims(email="intruder@gmail.com", hd="clockwork-av.com"))
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(token, "N")


async def test_nonce_mismatch_rejected(signer):
    token = _sign(signer, _claims(nonce="attacker-nonce"))
    with pytest.raises(oidc.OidcError):
        await oidc.verify_id_token(token, "N")  # expected nonce differs → replay/CSRF binding fails

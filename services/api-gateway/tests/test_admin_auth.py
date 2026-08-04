import time

from api_gateway.admin import auth
from api_gateway.admin.auth import Principal


def test_role_ordering():
    assert Principal("u", "OPERATOR").has_role("VIEWER")
    assert Principal("u", "OPERATOR").has_role("OPERATOR")
    assert not Principal("u", "VIEWER").has_role("OPERATOR")
    assert Principal("u", "ADMIN").has_role("OPERATOR")


def test_token_roundtrip():
    tok = auth.issue_dev_token("alice", "ANALYST")
    p = auth.authenticate(tok)
    assert p is not None and p.sub == "alice" and p.role == "ANALYST"


def test_tampered_token_rejected():
    tok = auth.issue_dev_token("alice", "ADMIN")
    body, _sig = tok.split(".", 1)
    forged = f"{body}.deadbeef"
    assert auth.authenticate(forged) is None


def test_expired_token_rejected():
    tok = auth.issue_dev_token("bob", "VIEWER", ttl_seconds=-1)
    assert auth.authenticate(tok) is None


def test_none_and_garbage_tokens():
    assert auth.authenticate(None) is None
    assert auth.authenticate("") is None
    assert auth.authenticate("not-a-token") is None


def test_role_escalation_via_forged_payload_fails():
    # Re-signing requires the secret; a hand-built payload with a bogus signature is rejected.
    import base64
    import json
    body = base64.urlsafe_b64encode(
        json.dumps({"sub": "x", "role": "ADMIN", "exp": int(time.time()) + 999}).encode()
    ).rstrip(b"=").decode()
    assert auth.authenticate(f"{body}.AAAA") is None

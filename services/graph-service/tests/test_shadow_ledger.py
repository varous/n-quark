"""Phase 1 Shadow Ledger tests — updated to the Phase 1.1 completeness-aware detector API.

Covers the deterministic detector (merge, hashing, transition mapping), observe() idempotency,
canonical-event linkage, epistemic status, the internal endpoints incl. ?trace=true, and the A–F
replay demonstration. Phase 1.1-specific behaviour (partial captures, field statuses, out-of-order,
disappearance) lives in test_shadow_capture_integrity.py.
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from graph_service.config import settings
from graph_service.deps import get_shadow_store
from graph_service.main import app
from graph_service.shadow_ledger import (
    EVENT_FIRST_SEEN,
    OBSERVED_PUBLIC_STATE,
    PUBLIC_FILL_RATIO_CHANGED,
    PUBLIC_PRICE_CHANGED,
    PUBLIC_TICKETS_SOLD_CHANGED,
    capture_hash,
    effective_state_hash,
    evaluate_present_capture,
    resolve_field_statuses,
)
from graph_service.shadow_store import ShadowStore

EVENT = "event:free-folk-nite"
SRC = "boshow"


def _statuses(values: dict) -> dict:
    return resolve_field_statuses(values, None, "PARTIAL")


def _eval(prev, values):
    return evaluate_present_capture(prev, values, _statuses(values))


# --------------------------------------------------------------------------- pure detector
def test_first_seen_and_no_change() -> None:
    ev = _eval(None, {"fill_ratio": 0.42})
    assert [t.transition_type for t in ev.transitions] == [EVENT_FIRST_SEEN]
    assert ev.first_seen and ev.effective_state["fill_ratio"] == 0.42
    again = _eval(ev.effective_state, {"fill_ratio": 0.42})
    assert again.transitions == []  # unchanged


def test_numeric_and_null_forms() -> None:
    # int-valued float collapses; zero is distinct from null in the effective hash
    assert effective_state_hash(_eval(None, {"tickets_sold": 400}).effective_state) == \
        effective_state_hash(_eval(None, {"tickets_sold": 400.0}).effective_state)
    zero = _eval(None, {"tickets_sold": 0}).effective_state
    missing = _eval(None, {}).effective_state
    assert zero["tickets_sold"] == 0 and missing["tickets_sold"] is None
    assert effective_state_hash(zero) != effective_state_hash(missing)


def test_money_ratio_timestamp_normalization() -> None:
    ev = _eval(None, {"price_min": 599.009, "fill_ratio": 0.4239, "starts_at": "2026-08-01T20:00:00Z"})
    assert ev.effective_state["price_min"] == 599.01
    assert ev.effective_state["fill_ratio"] == 0.424
    assert ev.effective_state["starts_at"] == "2026-08-01T20:00:00+00:00"


def test_fill_ratio_change_and_decrease_preserved() -> None:
    prev = _eval(None, {"tickets_sold": 400, "capacity": 1000, "fill_ratio": 0.40}).effective_state
    ev = _eval(prev, {"tickets_sold": 380, "capacity": 1000, "fill_ratio": 0.38})  # a decrease
    types = {t.transition_type for t in ev.transitions}
    assert PUBLIC_TICKETS_SOLD_CHANGED in types and PUBLIC_FILL_RATIO_CHANGED in types
    sold = next(t for t in ev.transitions if t.transition_type == PUBLIC_TICKETS_SOLD_CHANGED)
    assert sold.previous_value == 400 and sold.current_value == 380  # no refund inferred


def test_price_and_currency_collapse_to_one_transition() -> None:
    prev = _eval(None, {"price_min": 500, "currency": "INR"}).effective_state
    ev = _eval(prev, {"price_min": 500, "currency": "USD"})
    assert [t.transition_type for t in ev.transitions] == [PUBLIC_PRICE_CHANGED]


def test_capture_hash_reflects_only_observed_fields() -> None:
    a = {"fill_ratio": 0.5}
    assert capture_hash(a, _statuses(a)) == capture_hash(a, _statuses(a))
    assert capture_hash(a, _statuses(a)) != capture_hash({"fill_ratio": 0.6}, _statuses({"fill_ratio": 0.6}))


# --------------------------------------------------------------------------- store / observe
@pytest.fixture()
def store(tmp_path) -> ShadowStore:
    return ShadowStore(f"sqlite:///{tmp_path}/shadow.db")


def _observe(store: ShadowStore, **fields):
    return store.observe(
        canonical_event_id=EVENT, source_id=SRC, source_record_id="rec-1",
        raw_state=fields, provenance={"source_url": "https://www.boshow.in/x"}, observation_id="obs-1",
    )


def test_observe_first_then_idempotent(store: ShadowStore) -> None:
    r1 = _observe(store, fill_ratio=0.42, tickets_sold=42, capacity=100)
    assert r1["noop"] is False
    assert [t["transition_type"] for t in r1["transitions"]] == [EVENT_FIRST_SEEN]
    assert r1["state"]["epistemic_status"] == OBSERVED_PUBLIC_STATE
    r2 = _observe(store, fill_ratio=0.42, tickets_sold=42, capacity=100)
    assert r2["noop"] is True and r2["transitions"] == []
    assert len(store.list_states(EVENT)) == 1 and len(store.list_transitions(EVENT)) == 1


def test_observe_value_change_persists_both_states(store: ShadowStore) -> None:
    _observe(store, fill_ratio=0.42)
    r = _observe(store, fill_ratio=0.57)
    assert [t["transition_type"] for t in r["transitions"]] == [PUBLIC_FILL_RATIO_CHANGED]
    states = store.list_states(EVENT)
    assert len(states) == 2 and states[1]["previous_state_id"] == states[0]["id"]
    assert all(s["canonical_event_id"] == EVENT for s in states)


def test_replay_scenarios_A_to_F(store: ShadowStore) -> None:
    a = _observe(store, fill_ratio=0.42, tickets_sold=42, capacity=100, price_min=599, currency="INR")
    assert [t["transition_type"] for t in a["transitions"]] == [EVENT_FIRST_SEEN]
    b = _observe(store, fill_ratio=0.42, tickets_sold=42, capacity=100, price_min=599, currency="INR")
    assert b["noop"] is True
    c = _observe(store, fill_ratio=0.57, tickets_sold=57, capacity=100, price_min=599, currency="INR")
    assert PUBLIC_FILL_RATIO_CHANGED in {t["transition_type"] for t in c["transitions"]}
    d = _observe(store, fill_ratio=0.55, tickets_sold=55, capacity=100, price_min=599, currency="INR")
    sold = next(t for t in d["transitions"] if t["transition_type"] == PUBLIC_TICKETS_SOLD_CHANGED)
    assert sold["previous_value"] == 57 and sold["current_value"] == 55
    e = _observe(store, fill_ratio=0.80, tickets_sold=80, capacity=100, price_min=799, currency="INR")
    assert len({t["to_state_id"] for t in e["transitions"]}) == 1 and len(e["transitions"]) >= 2
    counts = (len(store.list_states(EVENT)), len(store.list_transitions(EVENT)))
    _observe(store, fill_ratio=0.80, tickets_sold=80, capacity=100, price_min=799, currency="INR")
    assert (len(store.list_states(EVENT)), len(store.list_transitions(EVENT))) == counts


# --------------------------------------------------------------------------- internal endpoints
@pytest.fixture()
def client(tmp_path) -> Generator[TestClient, None, None]:
    store = ShadowStore(f"sqlite:///{tmp_path}/api.db")
    app.dependency_overrides[get_shadow_store] = lambda: store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _post(client: TestClient, **fields):
    body = {"source_id": SRC, "source_record_id": "rec-1", **fields}
    return client.post(f"/v1/internal/events/{EVENT}/shadow-ledger/observe?trace=true", json=body)


def test_endpoint_observe_and_history_with_trace(client: TestClient) -> None:
    _post(client, fill_ratio=0.42, tickets_sold=42, capacity=100)
    r2 = _post(client, fill_ratio=0.57, tickets_sold=57, capacity=100).json()
    assert r2["trace"]["effective_hash"] and r2["trace"]["capture_hash"]

    led = client.get(f"/v1/internal/events/{EVENT}/shadow-ledger?trace=true").json()
    assert led["canonical_event_id"] == EVENT and len(led["states"]) == 2
    assert led["current_state"]["effective_state"]["fill_ratio"] == 0.57
    assert led["current_state"]["epistemic_status"] == OBSERVED_PUBLIC_STATE
    kinds = {t["transition_type"] for t in led["transitions"]}
    assert EVENT_FIRST_SEEN in kinds and PUBLIC_FILL_RATIO_CHANGED in kinds
    step = next(s for s in led["trace"]["evidence_chain"] if s["step"] == PUBLIC_FILL_RATIO_CHANGED)
    assert step["comparison"]["previous_value"] == 0.42 and step["comparison"]["current_value"] == 0.57
    assert step["effective_state"]["fill_ratio"] == 0.57


def test_endpoint_disabled_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "shadow_ledger_enabled", False)
    assert _post(client, fill_ratio=0.42).status_code == 503

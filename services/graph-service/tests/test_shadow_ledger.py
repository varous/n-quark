"""Phase 1 — Minimum Viable Shadow Ledger tests.

Covers the deterministic detector (normalization, hashing, comparison, transition mapping,
disappearance), the persistence + observe() idempotency, canonical-event linkage, epistemic status,
the internal endpoints incl. ?trace=true, and the A–F replay demonstration.
"""

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from graph_service.config import settings
from graph_service.deps import get_shadow_store
from graph_service.main import app
from graph_service.shadow_ledger import (
    EVENT_DISAPPEARED,
    EVENT_FIRST_SEEN,
    EVENT_REAPPEARED,
    OBSERVED_PUBLIC_STATE,
    PUBLIC_FILL_RATIO_CHANGED,
    PUBLIC_PRICE_CHANGED,
    PUBLIC_TICKETS_SOLD_CHANGED,
    detect_transitions,
    normalize_state,
    state_hash,
)
from graph_service.shadow_store import ShadowStore

EVENT = "event:free-folk-nite"
SRC = "boshow"


# --------------------------------------------------------------------------- pure detector
def test_normalization_null_vs_zero_and_numeric_forms() -> None:
    zero = normalize_state({"tickets_sold": 0, "capacity": 50})
    missing = normalize_state({"capacity": 50})  # tickets_sold absent -> None
    assert zero["tickets_sold"] == 0 and missing["tickets_sold"] is None
    assert state_hash(zero) != state_hash(missing)  # zero is distinct from null
    # 400 == 400.0 after normalization (int-valued float collapses)
    assert state_hash(normalize_state({"tickets_sold": 400})) == state_hash(
        normalize_state({"tickets_sold": 400.0})
    )


def test_normalization_money_ratio_and_timestamp() -> None:
    s = normalize_state({"price_min": 599.009, "fill_ratio": 0.4239, "starts_at": "2026-08-01T20:00:00Z"})
    assert s["price_min"] == 599.01  # money -> 2dp
    assert s["fill_ratio"] == 0.424  # ratio -> 3dp
    assert s["starts_at"] == "2026-08-01T20:00:00+00:00"  # Z normalized


def test_hash_is_order_independent_and_volatile_free() -> None:
    a = normalize_state({"price_min": 500, "venue": "Skinny Mos"})
    b = normalize_state({"venue": "Skinny Mos", "price_min": 500})
    assert state_hash(a) == state_hash(b)


def test_first_seen_and_no_change() -> None:
    s1 = normalize_state({"fill_ratio": 0.42})
    assert [t.transition_type for t in detect_transitions(None, s1)] == [EVENT_FIRST_SEEN]
    assert detect_transitions(s1, normalize_state({"fill_ratio": 0.42})) == []  # unchanged


def test_fill_ratio_change_and_decrease_are_value_changes() -> None:
    prev = normalize_state({"tickets_sold": 400, "capacity": 1000, "fill_ratio": 0.40})
    new = normalize_state({"tickets_sold": 380, "capacity": 1000, "fill_ratio": 0.38})  # decrease
    types = {t.transition_type for t in detect_transitions(prev, new)}
    assert PUBLIC_TICKETS_SOLD_CHANGED in types and PUBLIC_FILL_RATIO_CHANGED in types
    sold = next(t for t in detect_transitions(prev, new) if t.transition_type == PUBLIC_TICKETS_SOLD_CHANGED)
    assert sold.previous_value == 400 and sold.current_value == 380  # decrease preserved, no refund inferred


def test_price_and_currency_collapse_to_one_price_transition() -> None:
    prev = normalize_state({"price_min": 500, "currency": "INR"})
    new = normalize_state({"price_min": 500, "currency": "USD"})
    ts = detect_transitions(prev, new)
    assert [t.transition_type for t in ts] == [PUBLIC_PRICE_CHANGED]


def test_null_to_value_and_value_to_null() -> None:
    assert detect_transitions(normalize_state({}), normalize_state({"fill_ratio": 0.5}))[0].transition_type == PUBLIC_FILL_RATIO_CHANGED
    assert detect_transitions(normalize_state({"fill_ratio": 0.5}), normalize_state({}))[0].transition_type == PUBLIC_FILL_RATIO_CHANGED


def test_disappearance_requires_authoritative_and_threshold() -> None:
    present = normalize_state({"fill_ratio": 0.5})
    # a single failed capture never disappears the event
    absent_fail = normalize_state({}, present=False, absence_reason="capture_failure", prev_consecutive_absent=0)
    assert detect_transitions(present, absent_fail) == []
    # authoritative absence, threshold 2: first absent -> nothing, second -> EVENT_DISAPPEARED
    a1 = normalize_state({}, present=False, absence_reason="record_absent", prev_consecutive_absent=0)
    assert detect_transitions(present, a1, disappearance_threshold=2) == []
    a2 = normalize_state({}, present=False, absence_reason="record_absent", prev_consecutive_absent=1)
    assert [t.transition_type for t in detect_transitions(a1, a2, disappearance_threshold=2)] == [EVENT_DISAPPEARED]
    # explicit removal disappears immediately
    removed = normalize_state({}, present=False, absence_reason="explicitly_removed")
    assert [t.transition_type for t in detect_transitions(present, removed)] == [EVENT_DISAPPEARED]
    # reappearance after a real disappearance
    back = normalize_state({"fill_ratio": 0.6})
    assert [t.transition_type for t in detect_transitions(a2, back, disappearance_threshold=2)] == [EVENT_REAPPEARED]


# --------------------------------------------------------------------------- store / observe
@pytest.fixture()
def store(tmp_path) -> ShadowStore:
    return ShadowStore(f"sqlite:///{tmp_path}/shadow.db")


def _observe(store: ShadowStore, **fields):
    absent = fields.pop("_absent", False)
    reason = fields.pop("_reason", None)
    return store.observe(
        canonical_event_id=EVENT, source_id=SRC, source_record_id="rec-1",
        raw_state=fields, present=not absent, absence_reason=reason,
        provenance={"source_url": "https://www.boshow.in/x"}, observation_id="obs-1",
    )


def test_observe_first_then_idempotent(store: ShadowStore) -> None:
    r1 = _observe(store, fill_ratio=0.42, tickets_sold=42, capacity=100)
    assert r1["noop"] is False
    assert [t["transition_type"] for t in r1["transitions"]] == [EVENT_FIRST_SEEN]
    assert r1["state"]["epistemic_status"] == OBSERVED_PUBLIC_STATE
    # identical re-ingest -> no new state, no transition
    r2 = _observe(store, fill_ratio=0.42, tickets_sold=42, capacity=100)
    assert r2["noop"] is True and r2["transitions"] == []
    assert len(store.list_states(EVENT)) == 1
    assert len(store.list_transitions(EVENT)) == 1


def test_observe_value_change_persists_both_states(store: ShadowStore) -> None:
    _observe(store, fill_ratio=0.42)
    r = _observe(store, fill_ratio=0.57)
    assert r["noop"] is False
    assert [t["transition_type"] for t in r["transitions"]] == [PUBLIC_FILL_RATIO_CHANGED]
    states = store.list_states(EVENT)
    assert len(states) == 2  # both immutable states preserved
    assert states[1]["previous_state_id"] == states[0]["id"]  # linked chain
    assert all(s["canonical_event_id"] == EVENT for s in states)  # linkage


def test_observe_is_idempotent_on_replay_of_current_state(store: ShadowStore) -> None:
    _observe(store, fill_ratio=0.42)
    _observe(store, fill_ratio=0.57)
    before = (len(store.list_states(EVENT)), len(store.list_transitions(EVENT)))
    _observe(store, fill_ratio=0.57)  # replay current state
    _observe(store, fill_ratio=0.57)
    assert (len(store.list_states(EVENT)), len(store.list_transitions(EVENT))) == before


def test_replay_scenarios_A_to_F(store: ShadowStore) -> None:
    # A: first observation -> EVENT_FIRST_SEEN
    a = _observe(store, fill_ratio=0.42, tickets_sold=42, capacity=100, price_min=599, currency="INR")
    assert [t["transition_type"] for t in a["transitions"]] == [EVENT_FIRST_SEEN]
    # B: identical re-ingest -> no duplicate state/transition
    b = _observe(store, fill_ratio=0.42, tickets_sold=42, capacity=100, price_min=599, currency="INR")
    assert b["noop"] is True
    # C: public value change
    c = _observe(store, fill_ratio=0.57, tickets_sold=57, capacity=100, price_min=599, currency="INR")
    assert PUBLIC_FILL_RATIO_CHANGED in {t["transition_type"] for t in c["transitions"]}
    # D: correction / decrease preserved, no refund assumed
    d = _observe(store, fill_ratio=0.55, tickets_sold=55, capacity=100, price_min=599, currency="INR")
    sold = next(t for t in d["transitions"] if t["transition_type"] == PUBLIC_TICKETS_SOLD_CHANGED)
    assert sold["previous_value"] == 57 and sold["current_value"] == 55
    # E: multiple fields change together, all linked to one new state
    e = _observe(store, fill_ratio=0.80, tickets_sold=80, capacity=100, price_min=799, currency="INR")
    to_ids = {t["to_state_id"] for t in e["transitions"]}
    assert len(to_ids) == 1 and len(e["transitions"]) >= 2
    # F: reprocessing the whole sequence is idempotent (final state already current)
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
    r1 = _post(client, fill_ratio=0.42, tickets_sold=42, capacity=100).json()
    assert r1["noop"] is False and r1["trace"]["compared"] is False
    r2 = _post(client, fill_ratio=0.57, tickets_sold=57, capacity=100).json()
    assert r2["trace"]["previous_state_hash"] and r2["trace"]["new_state_hash"]

    led = client.get(f"/v1/internal/events/{EVENT}/shadow-ledger?trace=true").json()
    assert led["canonical_event_id"] == EVENT
    assert len(led["states"]) == 2
    assert led["current_state"]["normalized_state"]["fill_ratio"] == 0.57
    assert led["current_state"]["epistemic_status"] == OBSERVED_PUBLIC_STATE
    kinds = {t["transition_type"] for t in led["transitions"]}
    assert EVENT_FIRST_SEEN in kinds and PUBLIC_FILL_RATIO_CHANGED in kinds
    # trace shows the full evidence chain
    chain = led["trace"]["evidence_chain"]
    step = next(s for s in chain if s["step"] == PUBLIC_FILL_RATIO_CHANGED)
    assert step["comparison"]["previous_value"] == 0.42
    assert step["comparison"]["current_value"] == 0.57
    assert step["normalized_commercial_state"]["fill_ratio"] == 0.57


def test_endpoint_disabled_returns_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "shadow_ledger_enabled", False)
    resp = _post(client, fill_ratio=0.42)
    assert resp.status_code == 503

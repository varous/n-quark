"""Phase 1.1 — Capture Completeness and Transition Integrity.

Proves incomplete / partial / failed / out-of-order captures cannot fabricate transitions or
corrupt current state, and that disappearance is driven only by authoritative absence evidence.
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from graph_service.deps import get_shadow_store
from graph_service.main import app
from graph_service.shadow_ledger import (
    EVENT_DATE_CHANGED,
    EVENT_DISAPPEARED,
    EVENT_FIRST_SEEN,
    EVENT_REAPPEARED,
    PUBLIC_AVAILABILITY_CHANGED,
    PUBLIC_FILL_RATIO_CHANGED,
)
from graph_service.shadow_store import ShadowStore

EVENT = "event:x"
SRC = "boshow"
T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


@pytest.fixture()
def store(tmp_path) -> ShadowStore:
    return ShadowStore(f"sqlite:///{tmp_path}/ci.db")


def obs(store, *, completeness="PARTIAL", capture_status=None, field_status=None, present=True,
        absence_reason=None, observed_at=None, threshold=2, **values):
    return store.observe(
        canonical_event_id=EVENT, source_id=SRC, source_record_id="rec-1", raw_state=values,
        snapshot_completeness=completeness, capture_status=capture_status, field_status=field_status,
        present=present, absence_reason=absence_reason, observed_at=observed_at,
        disappearance_threshold=threshold, provenance={},
    )


def _types(result) -> list[str]:
    return [t["transition_type"] for t in result["transitions"]]


# ---- completeness (1-4) -------------------------------------------------------------------------
def test_1_default_completeness_is_partial(store) -> None:
    r = obs(store, fill_ratio=0.4)
    assert r["state"]["snapshot_completeness"] == "PARTIAL"


def test_2_3_complete_and_partial_accepted(store) -> None:
    assert obs(store, completeness="COMPLETE", fill_ratio=0.4)["state"]["snapshot_completeness"] == "COMPLETE"
    assert obs(store, completeness="PARTIAL", fill_ratio=0.5)["state"]["snapshot_completeness"] == "PARTIAL"


# (4 invalid-completeness rejection is an endpoint concern — see test_endpoint_rejects_invalid_enums)


# ---- field statuses (5-10) ----------------------------------------------------------------------
def test_5_observed_value_updates_field(store) -> None:
    obs(store, fill_ratio=0.4)
    assert PUBLIC_FILL_RATIO_CHANGED in _types(obs(store, fill_ratio=0.6))


def test_6_observed_null_creates_value_to_null_where_allowed(store) -> None:
    obs(store, field_status={"availability": "OBSERVED_VALUE"}, availability="FEW_LEFT")
    r = obs(store, field_status={"availability": "OBSERVED_NULL"}, availability=None)
    t = next(t for t in r["transitions"] if t["transition_type"] == PUBLIC_AVAILABILITY_CHANGED)
    assert t["previous_value"] == "FEW_LEFT" and t["current_value"] is None


def test_7_not_observed_creates_no_transition(store) -> None:
    obs(store, fill_ratio=0.4, capacity=100)
    r = obs(store, field_status={"capacity": "NOT_OBSERVED"}, fill_ratio=0.4, capacity=None)
    assert r["transitions"] == [] or all("CAPACITY" not in t for t in _types(r))
    assert store.list_states(EVENT)[-1]["effective_state"]["capacity"] == 100  # carried forward


def test_8_extraction_failed_creates_no_transition(store) -> None:
    obs(store, fill_ratio=0.4)
    r = obs(store, field_status={"fill_ratio": "EXTRACTION_FAILED"}, fill_ratio=None)
    assert r["noop"] is True or PUBLIC_FILL_RATIO_CHANGED not in _types(r)
    assert store.list_states(EVENT)[-1]["effective_state"]["fill_ratio"] == 0.4


def test_9_not_supported_creates_no_transition(store) -> None:
    obs(store, fill_ratio=0.4)
    r = obs(store, field_status={"availability": "NOT_SUPPORTED"}, fill_ratio=0.4, availability=None)
    assert not any("AVAILABILITY" in t for t in _types(r))


def test_10_zero_distinct_from_null(store) -> None:
    obs(store, tickets_sold=5)
    r = obs(store, field_status={"tickets_sold": "OBSERVED_VALUE"}, tickets_sold=0)
    assert any(t["current_value"] == 0 for t in r["transitions"])


# ---- partial-state merging (11-14) --------------------------------------------------------------
def test_11_and_12_omitted_date_carried_only_fill_ratio_changes(store) -> None:
    obs(store, completeness="COMPLETE",
        field_status={"starts_at": "OBSERVED_VALUE", "fill_ratio": "OBSERVED_VALUE"},
        starts_at="2026-09-10T19:00:00", fill_ratio=0.20)
    r = obs(store, completeness="PARTIAL",
            field_status={"fill_ratio": "OBSERVED_VALUE", "starts_at": "NOT_OBSERVED"}, fill_ratio=0.57)
    assert _types(r) == [PUBLIC_FILL_RATIO_CHANGED]           # only fill ratio
    assert EVENT_DATE_CHANGED not in _types(r)                # the headline bug: no false date change
    assert store.list_states(EVENT)[-1]["effective_state"]["starts_at"] == "2026-09-10T19:00:00"  # carried


def test_13_carried_forward_marked_not_observed_in_capture(store) -> None:
    obs(store, starts_at="2026-09-10T19:00:00", fill_ratio=0.2)
    obs(store, field_status={"fill_ratio": "OBSERVED_VALUE", "starts_at": "NOT_OBSERVED"}, fill_ratio=0.5)
    latest = store.list_states(EVENT)[-1]
    assert latest["field_status"]["starts_at"] == "NOT_OBSERVED"  # not claimed as observed again
    assert latest["field_status"]["fill_ratio"] == "OBSERVED_VALUE"


def test_14_reingest_same_partial_is_idempotent(store) -> None:
    obs(store, fill_ratio=0.4)
    before = (len(store.list_states(EVENT)), len(store.list_transitions(EVENT)))
    obs(store, fill_ratio=0.4)
    assert (len(store.list_states(EVENT)), len(store.list_transitions(EVENT))) == before


# ---- complete snapshots (15-17) -----------------------------------------------------------------
def test_15_complete_explicit_null_follows_policy(store) -> None:
    # availability allows explicit null (transition); starts_at does not (suppressed)
    obs(store, availability="FEW_LEFT", starts_at="2026-09-10T19:00:00",
        field_status={"availability": "OBSERVED_VALUE", "starts_at": "OBSERVED_VALUE"})
    r = obs(store, completeness="COMPLETE",
            field_status={"availability": "OBSERVED_NULL", "starts_at": "OBSERVED_NULL"},
            availability=None, starts_at=None)
    assert PUBLIC_AVAILABILITY_CHANGED in _types(r)
    assert EVENT_DATE_CHANGED not in _types(r)
    assert any(s["reason"] == "EXPLICIT_NULL_NOT_ALLOWED" for s in r["suppressed"])
    assert store.list_states(EVENT)[-1]["effective_state"]["starts_at"] == "2026-09-10T19:00:00"  # kept


def test_16_complete_absent_non_removable_field_no_false_null(store) -> None:
    obs(store, starts_at="2026-09-10T19:00:00", fill_ratio=0.2)
    # COMPLETE but starts_at simply not in this capture's field_status/values -> inferred NOT_OBSERVED
    r = obs(store, completeness="COMPLETE", field_status={"fill_ratio": "OBSERVED_VALUE"}, fill_ratio=0.3)
    assert EVENT_DATE_CHANGED not in _types(r)
    assert store.list_states(EVENT)[-1]["effective_state"]["starts_at"] == "2026-09-10T19:00:00"


def test_17_complete_updates_multiple_fields(store) -> None:
    obs(store, fill_ratio=0.2, tickets_sold=20, price_min=500, currency="INR")
    r = obs(store, completeness="COMPLETE", fill_ratio=0.5, tickets_sold=50, price_min=700, currency="INR")
    assert len({t["to_state_id"] for t in r["transitions"]}) == 1 and len(r["transitions"]) >= 3


# ---- out-of-order (18-21) -----------------------------------------------------------------------
def test_18_19_older_capture_does_not_replace_or_emit_forward(store) -> None:
    obs(store, fill_ratio=0.5, observed_at=T0)
    r_old = obs(store, fill_ratio=0.9, observed_at=T0 - timedelta(hours=1))
    assert r_old["out_of_order"] is True and r_old["transitions"] == []
    assert store.latest_state(EVENT, SRC, "rec-1")["effective_state"]["fill_ratio"] == 0.5  # current unchanged


def test_20_same_timestamp_same_payload_idempotent(store) -> None:
    obs(store, fill_ratio=0.5, observed_at=T0)
    r = obs(store, fill_ratio=0.5, observed_at=T0)
    assert r["noop"] is True


def test_21_same_timestamp_conflicting_payload_is_deterministic(store) -> None:
    obs(store, fill_ratio=0.5, observed_at=T0)
    r = obs(store, fill_ratio=0.9, observed_at=T0)
    assert r["out_of_order"] is True and r["transitions"] == []
    assert store.latest_state(EVENT, SRC, "rec-1")["effective_state"]["fill_ratio"] == 0.5


# ---- disappearance (22-28) ----------------------------------------------------------------------
def test_22_23_failures_do_not_increment(store) -> None:
    obs(store, fill_ratio=0.5, observed_at=T0)
    r1 = obs(store, present=False, absence_reason="capture_failure", observed_at=T0 + timedelta(hours=1))
    r2 = obs(store, present=False, absence_reason="parser_failure", observed_at=T0 + timedelta(hours=2))
    assert r1["persisted"] is False and r2["persisted"] is False
    assert r1["absence_count"] == 0 and r2["absence_count"] == 0


def test_24_25_absence_increments_and_observation_resets(store) -> None:
    obs(store, fill_ratio=0.5, observed_at=T0)
    a1 = obs(store, capture_status="CAPTURE_SUCCESS_RECORD_ABSENT", observed_at=T0 + timedelta(hours=1))
    assert a1["absence_count"] == 1
    back = obs(store, fill_ratio=0.5, observed_at=T0 + timedelta(hours=2))  # present again
    assert back["absence_count"] == 0


def test_26_27_threshold_emits_once_no_duplicate(store) -> None:
    obs(store, fill_ratio=0.5, observed_at=T0)
    a1 = obs(store, capture_status="CAPTURE_SUCCESS_RECORD_ABSENT", observed_at=T0 + timedelta(hours=1), threshold=2)
    a2 = obs(store, capture_status="CAPTURE_SUCCESS_RECORD_ABSENT", observed_at=T0 + timedelta(hours=2), threshold=2)
    a3 = obs(store, capture_status="CAPTURE_SUCCESS_RECORD_ABSENT", observed_at=T0 + timedelta(hours=3), threshold=2)
    assert _types(a1) == [] and _types(a2) == [EVENT_DISAPPEARED] and _types(a3) == []


def test_28_reappearance_emits_once(store) -> None:
    obs(store, fill_ratio=0.5, observed_at=T0)
    obs(store, capture_status="CAPTURE_SUCCESS_RECORD_ABSENT", observed_at=T0 + timedelta(hours=1), threshold=2)
    obs(store, capture_status="CAPTURE_SUCCESS_RECORD_ABSENT", observed_at=T0 + timedelta(hours=2), threshold=2)
    back = obs(store, fill_ratio=0.5, observed_at=T0 + timedelta(hours=3), threshold=2)
    assert _types(back).count(EVENT_REAPPEARED) == 1


def test_explicitly_removed_disappears_immediately(store) -> None:
    obs(store, fill_ratio=0.5, observed_at=T0)
    r = obs(store, capture_status="EXPLICITLY_REMOVED", observed_at=T0 + timedelta(hours=1), threshold=2)
    assert _types(r) == [EVENT_DISAPPEARED]


# ---- endpoint validation (item 4) + compatibility -----------------------------------------------
@pytest.fixture()
def client(tmp_path) -> Generator[TestClient, None, None]:
    st = ShadowStore(f"sqlite:///{tmp_path}/api.db")
    app.dependency_overrides[get_shadow_store] = lambda: st
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_endpoint_rejects_invalid_enums(client: TestClient) -> None:
    base = {"source_id": SRC, "source_record_id": "r1", "fill_ratio": 0.4}
    assert client.post(f"/v1/internal/events/{EVENT}/shadow-ledger/observe",
                       json={**base, "snapshot_completeness": "SORTA"}).status_code == 422
    assert client.post(f"/v1/internal/events/{EVENT}/shadow-ledger/observe",
                       json={**base, "capture_status": "MAYBE"}).status_code == 422
    assert client.post(f"/v1/internal/events/{EVENT}/shadow-ledger/observe",
                       json={**base, "field_status": {"fill_ratio": "GUESSED"}}).status_code == 422


def test_first_seen_still_works_end_to_end(client: TestClient) -> None:
    body = client.post(f"/v1/internal/events/{EVENT}/shadow-ledger/observe?trace=true",
                       json={"source_id": SRC, "source_record_id": "r1", "fill_ratio": 0.4}).json()
    assert [t["transition_type"] for t in body["transitions"]] == [EVENT_FIRST_SEEN]
    assert body["trace"]["snapshot_completeness"] == "PARTIAL"

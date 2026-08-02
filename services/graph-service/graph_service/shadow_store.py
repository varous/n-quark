"""Shadow Ledger persistence + the observe() orchestration (Phase 1 + Phase 1.1).

Owns its own engine (mirroring PostgresGraphStore) so the GraphStore abstraction is untouched.
``observe`` is the single write path. Phase 1.1 makes it capture-integrity-aware:

- partial captures merge onto the previous effective state (unobserved fields carried forward);
- out-of-order / conflicting captures are audited but never corrupt the current state;
- disappearance is driven by capture_status + a consecutive authoritative-absence counter, never by
  missing fields or failed captures.

Portable to the SQLite used by unit tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from graph_service.shadow_ledger import (
    AUTHORITATIVE_ABSENCE,
    CAPTURE_FAILED,
    CAPTURE_SUCCESS_RECORD_ABSENT,
    CAPTURE_SUCCESS_RECORD_PRESENT,
    CONFLICTING_TIMESTAMP,
    DETECTOR_VERSION,
    DUPLICATE_STATE,
    EVENT_REAPPEARED,
    EXPLICITLY_REMOVED,
    NON_AUTHORITATIVE,
    NOT_CHECKED,
    OBSERVED_PUBLIC_STATE,
    OUT_OF_ORDER,
    PARSER_FAILED,
    PARTIAL,
    SOURCE_UNAVAILABLE,
    Transition,
    absence_step,
    capture_hash,
    dedup_key,
    effective_state_hash,
    evaluate_present_capture,
    resolve_field_statuses,
)


def _uuid() -> str:
    return uuid.uuid4().hex


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _aware(dt: datetime | None) -> datetime | None:
    # SQLite (tests) returns naive datetimes; treat stored times as UTC so comparisons are safe
    # across SQLite and Postgres (which returns tz-aware).
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


# Map the Phase 1 present/absence_reason inputs onto the Phase 1.1 capture_status enum.
_REASON_TO_STATUS = {
    "record_absent": CAPTURE_SUCCESS_RECORD_ABSENT,
    "not_found": CAPTURE_SUCCESS_RECORD_ABSENT,
    "explicitly_removed": EXPLICITLY_REMOVED,
    "capture_failure": CAPTURE_FAILED,
    "source_unavailable": SOURCE_UNAVAILABLE,
    "parser_failure": PARSER_FAILED,
}


def _derive_capture_status(present: bool, absence_reason: str | None, capture_status: str | None) -> str:
    if capture_status:
        return capture_status
    if present:
        return CAPTURE_SUCCESS_RECORD_PRESENT
    return _REASON_TO_STATUS.get(absence_reason or "", NOT_CHECKED)


class ShadowStore:
    def __init__(self, url: str) -> None:
        from sqlalchemy import create_engine, select
        from sqlalchemy.orm import sessionmaker

        from graph_service.models import Base, ShadowStateRecord, ShadowTransitionRecord

        self._select = select
        self._State = ShadowStateRecord
        self._Transition = ShadowTransitionRecord

        connect_args: dict[str, object] = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        self._engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        Base.metadata.create_all(self._engine)  # alembic owns prod; check-first fallback for dev/tests
        self._Session = sessionmaker(bind=self._engine, autoflush=False, expire_on_commit=False)

    # ---- reads ----------------------------------------------------------------------------------
    def _latest(self, session, event_id, source_id, source_record_id, *, forward_only=True):
        stmt = self._select(self._State).where(
            self._State.canonical_event_id == event_id, self._State.source_id == source_id
        )
        if source_record_id is not None:
            stmt = stmt.where(self._State.source_record_id == source_record_id)
        if forward_only:
            stmt = stmt.where(self._State.out_of_order.is_(False))
        stmt = stmt.order_by(
            self._State.observed_at.desc(), self._State.created_at.desc(), self._State.id.desc()
        )
        return session.scalars(stmt.limit(1)).first()

    def latest_state(self, event_id, source_id, source_record_id=None) -> dict | None:
        with self._Session() as session:
            row = self._latest(session, event_id, source_id, source_record_id)
            return self._state_dict(row) if row else None

    def list_states(self, event_id, source_id=None, limit=200) -> list[dict]:
        with self._Session() as session:
            stmt = self._select(self._State).where(self._State.canonical_event_id == event_id)
            if source_id:
                stmt = stmt.where(self._State.source_id == source_id)
            stmt = stmt.order_by(self._State.observed_at.asc(), self._State.created_at.asc()).limit(limit)
            return [self._state_dict(r) for r in session.scalars(stmt)]

    def list_transitions(self, event_id, source_id=None, limit=500) -> list[dict]:
        with self._Session() as session:
            stmt = self._select(self._Transition).where(self._Transition.canonical_event_id == event_id)
            if source_id:
                stmt = stmt.where(self._Transition.source_id == source_id)
            stmt = stmt.order_by(self._Transition.detected_at.asc(), self._Transition.created_at.asc()).limit(limit)
            return [self._transition_dict(r) for r in session.scalars(stmt)]

    # ---- write path -----------------------------------------------------------------------------
    def observe(
        self,
        *,
        canonical_event_id: str,
        source_id: str,
        raw_state: dict[str, Any],
        source_record_id: str | None = None,
        observation_id: str | None = None,
        observed_at: datetime | None = None,
        provenance: dict[str, Any] | None = None,
        epistemic_status: str = OBSERVED_PUBLIC_STATE,
        field_status: dict[str, str] | None = None,
        snapshot_completeness: str | None = None,
        capture_status: str | None = None,
        present: bool = True,
        absence_reason: str | None = None,
        disappearance_threshold: int = 2,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        observed_at = observed_at or now
        provenance = provenance or {}
        completeness = snapshot_completeness or PARTIAL
        status = _derive_capture_status(present, absence_reason, capture_status)
        statuses = resolve_field_statuses(raw_state, field_status, completeness)
        cap_hash = capture_hash(raw_state, statuses)

        with self._Session() as session, session.begin():
            latest = self._latest(session, canonical_event_id, source_id, source_record_id)

            # --- temporal ordering (Phase 1.1) -------------------------------------------------
            latest_observed = _aware(latest.observed_at) if latest is not None else None
            if latest is not None and observed_at < latest_observed:
                return self._audit_row(
                    session, canonical_event_id, source_id, source_record_id, observation_id,
                    observed_at, raw_state, statuses, completeness, status, cap_hash, provenance,
                    epistemic_status, now, reason=OUT_OF_ORDER, latest=latest,
                )
            if latest is not None and observed_at == latest_observed:
                if latest.capture_hash == cap_hash:
                    return self._result(True, False, latest, [], [{"reason": DUPLICATE_STATE}],
                                        status, latest.absence_count, self._eq_ts_trace(latest, "duplicate"))
                return self._audit_row(
                    session, canonical_event_id, source_id, source_record_id, observation_id,
                    observed_at, raw_state, statuses, completeness, status, cap_hash, provenance,
                    epistemic_status, now, reason=CONFLICTING_TIMESTAMP, latest=latest,
                )

            prev_effective = latest.normalized_state if latest else None
            prev_absence = latest.absence_count if latest else 0
            prev_disappeared = bool(
                latest and (latest.absence_count >= disappearance_threshold
                            or latest.capture_status == EXPLICITLY_REMOVED)
            )

            # --- non-authoritative failures: never persist a state, never count -----------------
            if status in NON_AUTHORITATIVE:
                trace = {"capture_status": status, "result": "capture failure — no state change",
                         "absence_counting": "skipped (non-authoritative)"}
                return self._result(False, False, None, [], [{"reason": status}], status, prev_absence, trace)

            # --- authoritative absence: disappearance bookkeeping ------------------------------
            if status in AUTHORITATIVE_ABSENCE:
                new_count, _disappeared, transitions, suppressed = absence_step(
                    prev_absence, prev_disappeared, status, disappearance_threshold
                )
                effective = dict(prev_effective or {})  # commercial state carried forward, unchanged
                eff_hash = effective_state_hash(effective)
                row = self._insert_state(
                    session, canonical_event_id, source_id, source_record_id, observation_id,
                    observed_at, eff_hash, effective, epistemic_status, provenance,
                    latest.id if latest else None, now, completeness, status, statuses, cap_hash,
                    False, new_count,
                )
                persisted = self._insert_transitions(session, canonical_event_id, source_id,
                                                     latest.id if latest else None, row.id, transitions, now)
                trace = {"capture_status": status, "absence_count": new_count,
                         "threshold": disappearance_threshold, "previous_effective_hash": (latest.state_hash if latest else None),
                         "effective_hash": eff_hash, "result": f"{len(persisted)} transition(s)"}
                return self._result(False, True, row, persisted, suppressed, status, new_count, trace)

            # --- present capture: merge + safe transition detection ----------------------------
            ev = evaluate_present_capture(prev_effective, raw_state, statuses)
            eff_hash = effective_state_hash(ev.effective_state)
            transitions = list(ev.transitions)
            if prev_disappeared and not ev.first_seen:
                transitions.insert(0, Transition(EVENT_REAPPEARED, confidence=0.8))

            # no-op only when the previous *forward* row was itself present and unchanged
            if (latest is not None and not prev_disappeared
                    and latest.capture_status == CAPTURE_SUCCESS_RECORD_PRESENT
                    and latest.state_hash == eff_hash):
                trace = {"capture_status": status, "previous_effective_hash": latest.state_hash,
                         "effective_hash": eff_hash, "result": "unchanged (no-op)"}
                return self._result(True, False, latest, [], ev.suppressed, status, 0, trace)

            row = self._insert_state(
                session, canonical_event_id, source_id, source_record_id, observation_id,
                observed_at, eff_hash, ev.effective_state, epistemic_status, provenance,
                latest.id if latest else None, now, completeness, status, statuses, cap_hash,
                False, 0,  # present -> absence counter resets
            )
            persisted = self._insert_transitions(session, canonical_event_id, source_id,
                                                 latest.id if latest else None, row.id, transitions, now)
            trace = {
                "capture_status": status,
                "snapshot_completeness": completeness,
                "previous_effective_hash": (latest.state_hash if latest else None),
                "capture_hash": cap_hash,
                "effective_hash": eff_hash,
                "carried_forward": [
                    s["field"] for s in ev.suppressed if s["reason"] in ("FIELD_NOT_OBSERVED", "EXTRACTION_FAILED")
                ],
                "result": f"{'first seen; ' if ev.first_seen else ''}{len(persisted)} transition(s)",
            }
            return self._result(False, True, row, persisted, ev.suppressed, status, 0, trace)

    # ---- helpers --------------------------------------------------------------------------------
    def _insert_state(self, session, event_id, source_id, source_record_id, observation_id, observed_at,
                      eff_hash, effective, epistemic_status, provenance, previous_state_id, now,
                      completeness, status, statuses, cap_hash, out_of_order, absence_count):
        row = self._State(
            id=_uuid(), canonical_event_id=event_id, source_id=source_id,
            source_record_id=source_record_id, observation_id=observation_id, observed_at=observed_at,
            state_hash=eff_hash, normalized_state=effective, epistemic_status=epistemic_status,
            provenance=provenance, previous_state_id=previous_state_id, detector_version=DETECTOR_VERSION,
            created_at=now, snapshot_completeness=completeness, capture_status=status,
            field_status=statuses, capture_hash=cap_hash, out_of_order=out_of_order,
            absence_count=absence_count,
        )
        session.add(row)
        return row

    def _insert_transitions(self, session, event_id, source_id, from_state_id, to_state_id, transitions, now):
        persisted: list[dict] = []
        for t in transitions:
            key = dedup_key(to_state_id, t.transition_type, t.field_name)
            exists = session.scalars(
                self._select(self._Transition).where(self._Transition.dedup_key == key).limit(1)
            ).first()
            if exists is not None:
                continue
            row = self._Transition(
                id=_uuid(), canonical_event_id=event_id, source_id=source_id,
                from_state_id=from_state_id, to_state_id=to_state_id,
                transition_type=t.transition_type, field_name=t.field_name,
                previous_value=t.previous_value, current_value=t.current_value,
                detected_at=now, effective_at=None, confidence=t.confidence,
                epistemic_status=t.epistemic_status, detector_version=DETECTOR_VERSION,
                dedup_key=key, created_at=now,
            )
            session.add(row)
            persisted.append(self._transition_dict(row))
        return persisted

    def _audit_row(self, session, event_id, source_id, source_record_id, observation_id, observed_at,
                   raw_state, statuses, completeness, status, cap_hash, provenance, epistemic_status,
                   now, *, reason, latest):
        """Persist an out-of-order / conflicting capture for audit WITHOUT changing current state."""
        snapshot = {name: None for name in latest.normalized_state} if latest else {}
        from graph_service.shadow_ledger import FIELD_SPECS
        for spec in FIELD_SPECS:
            if statuses.get(spec.name) == "OBSERVED_VALUE":
                snapshot[spec.name] = spec.normalizer(raw_state.get(spec.source_field))
        row = self._insert_state(
            session, event_id, source_id, source_record_id, observation_id, observed_at,
            effective_state_hash(snapshot), snapshot, epistemic_status, provenance,
            None, now, completeness, status, statuses, cap_hash, True, 0,
        )
        trace = {"result": f"audited ({reason}); current state unchanged",
                 "latest_observed_at": _iso(latest.observed_at) if latest else None,
                 "this_observed_at": _iso(observed_at)}
        return self._result(False, True, row, [], [{"reason": reason}], status, 0, trace, out_of_order=True)

    def _result(self, noop, persisted, state_row, transitions, suppressed, capture_status,
                absence_count, trace, out_of_order=False):
        return {
            "noop": noop,
            "persisted": persisted,
            "out_of_order": out_of_order,
            "capture_status": capture_status,
            "absence_count": absence_count,
            "state": self._state_dict(state_row) if state_row is not None else None,
            "transitions": transitions,
            "suppressed": suppressed,
            "trace": trace,
        }

    @staticmethod
    def _eq_ts_trace(latest, kind):
        return {"result": f"same timestamp, {kind}", "capture_hash": latest.capture_hash}

    # ---- serialization --------------------------------------------------------------------------
    def _state_dict(self, r) -> dict[str, Any]:
        return {
            "id": r.id, "canonical_event_id": r.canonical_event_id, "source_id": r.source_id,
            "source_record_id": r.source_record_id, "observation_id": r.observation_id,
            "observed_at": _iso(r.observed_at), "state_hash": r.state_hash,
            "effective_state_hash": r.state_hash, "capture_hash": r.capture_hash,
            "normalized_state": dict(r.normalized_state), "effective_state": dict(r.normalized_state),
            "field_status": dict(r.field_status or {}), "snapshot_completeness": r.snapshot_completeness,
            "capture_status": r.capture_status, "out_of_order": r.out_of_order,
            "absence_count": r.absence_count, "epistemic_status": r.epistemic_status,
            "provenance": dict(r.provenance), "previous_state_id": r.previous_state_id,
            "detector_version": r.detector_version, "created_at": _iso(r.created_at),
        }

    def _transition_dict(self, r) -> dict[str, Any]:
        return {
            "id": r.id, "canonical_event_id": r.canonical_event_id, "source_id": r.source_id,
            "from_state_id": r.from_state_id, "to_state_id": r.to_state_id,
            "transition_type": r.transition_type, "field_name": r.field_name,
            "previous_value": r.previous_value, "current_value": r.current_value,
            "detected_at": _iso(r.detected_at), "effective_at": _iso(r.effective_at),
            "confidence": r.confidence, "epistemic_status": r.epistemic_status,
            "detector_version": r.detector_version,
        }

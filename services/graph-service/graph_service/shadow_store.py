"""Shadow Ledger persistence + the observe() orchestration.

Owns its own engine (mirroring PostgresGraphStore) so the GraphStore abstraction is untouched.
``observe`` is the single write path: it reads the latest state, suppresses no-op re-captures by
state-hash, appends genuinely-distinct states, and records immutable, de-duplicated transitions.
Portable to the SQLite used by unit tests.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from graph_service.shadow_ledger import (
    DETECTOR_VERSION,
    OBSERVED_PUBLIC_STATE,
    Transition,
    dedup_key,
    detect_transitions,
    normalize_state,
    state_hash,
)


def _uuid() -> str:
    return uuid.uuid4().hex


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


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
    def _latest(self, session, event_id: str, source_id: str, source_record_id: str | None):
        stmt = (
            self._select(self._State)
            .where(self._State.canonical_event_id == event_id, self._State.source_id == source_id)
        )
        if source_record_id is not None:
            stmt = stmt.where(self._State.source_record_id == source_record_id)
        stmt = stmt.order_by(self._State.observed_at.desc(), self._State.created_at.desc(), self._State.id.desc())
        return session.scalars(stmt.limit(1)).first()

    def latest_state(self, event_id: str, source_id: str, source_record_id: str | None = None) -> dict | None:
        with self._Session() as session:
            row = self._latest(session, event_id, source_id, source_record_id)
            return self._state_dict(row) if row else None

    def list_states(self, event_id: str, source_id: str | None = None, limit: int = 200) -> list[dict]:
        with self._Session() as session:
            stmt = self._select(self._State).where(self._State.canonical_event_id == event_id)
            if source_id:
                stmt = stmt.where(self._State.source_id == source_id)
            stmt = stmt.order_by(self._State.observed_at.asc(), self._State.created_at.asc()).limit(limit)
            return [self._state_dict(r) for r in session.scalars(stmt)]

    def list_transitions(self, event_id: str, source_id: str | None = None, limit: int = 500) -> list[dict]:
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
        present: bool = True,
        absence_reason: str | None = None,
        disappearance_threshold: int = 2,
    ) -> dict[str, Any]:
        """Record one observed commercial state; detect + persist transitions. Idempotent on
        unchanged state (returns noop=True and emits nothing)."""
        now = datetime.now(UTC)
        observed_at = observed_at or now
        provenance = provenance or {}

        with self._Session() as session, session.begin():
            latest = self._latest(session, canonical_event_id, source_id, source_record_id)
            prev_norm: dict[str, Any] | None = latest.normalized_state if latest else None
            prev_cc = int(prev_norm.get("consecutive_absent", 0)) if prev_norm else 0

            normalized = normalize_state(
                raw_state, present=present, absence_reason=absence_reason, prev_consecutive_absent=prev_cc
            )
            new_hash = state_hash(normalized)

            trace = {
                "previous_state_id": latest.id if latest else None,
                "previous_state_hash": latest.state_hash if latest else None,
                "new_state_hash": new_hash,
                "compared": bool(latest),
            }

            if latest and latest.state_hash == new_hash:
                trace["result"] = "unchanged (no-op)"
                return {
                    "noop": True,
                    "state": self._state_dict(latest),
                    "transitions": [],
                    "trace": trace,
                }

            new_id = _uuid()
            state_row = self._State(
                id=new_id,
                canonical_event_id=canonical_event_id,
                source_id=source_id,
                source_record_id=source_record_id,
                observation_id=observation_id,
                observed_at=observed_at,
                state_hash=new_hash,
                normalized_state=normalized,
                epistemic_status=epistemic_status,
                provenance=provenance,
                previous_state_id=latest.id if latest else None,
                detector_version=DETECTOR_VERSION,
                created_at=now,
            )
            session.add(state_row)

            transitions: list[Transition] = detect_transitions(
                prev_norm, normalized, disappearance_threshold=disappearance_threshold
            )
            persisted: list[dict] = []
            for t in transitions:
                key = dedup_key(new_id, t.transition_type, t.field_name)
                exists = session.scalars(
                    self._select(self._Transition).where(self._Transition.dedup_key == key).limit(1)
                ).first()
                if exists is not None:
                    continue  # idempotent guard (belt-and-suspenders; no-op check usually prevents this)
                row = self._Transition(
                    id=_uuid(),
                    canonical_event_id=canonical_event_id,
                    source_id=source_id,
                    from_state_id=latest.id if latest else None,
                    to_state_id=new_id,
                    transition_type=t.transition_type,
                    field_name=t.field_name,
                    previous_value=t.previous_value,
                    current_value=t.current_value,
                    detected_at=now,
                    effective_at=None,
                    confidence=t.confidence,
                    epistemic_status=t.epistemic_status,
                    detector_version=DETECTOR_VERSION,
                    dedup_key=key,
                    created_at=now,
                )
                session.add(row)
                persisted.append(self._transition_dict(row))

            trace["result"] = f"persisted new state; {len(persisted)} transition(s)"
            return {
                "noop": False,
                "state": self._state_dict(state_row),
                "transitions": persisted,
                "trace": trace,
            }

    # ---- serialization --------------------------------------------------------------------------
    def _state_dict(self, r) -> dict[str, Any]:
        return {
            "id": r.id,
            "canonical_event_id": r.canonical_event_id,
            "source_id": r.source_id,
            "source_record_id": r.source_record_id,
            "observation_id": r.observation_id,
            "observed_at": _iso(r.observed_at),
            "state_hash": r.state_hash,
            "normalized_state": dict(r.normalized_state),
            "epistemic_status": r.epistemic_status,
            "provenance": dict(r.provenance),
            "previous_state_id": r.previous_state_id,
            "detector_version": r.detector_version,
            "created_at": _iso(r.created_at),
        }

    def _transition_dict(self, r) -> dict[str, Any]:
        return {
            "id": r.id,
            "canonical_event_id": r.canonical_event_id,
            "source_id": r.source_id,
            "from_state_id": r.from_state_id,
            "to_state_id": r.to_state_id,
            "transition_type": r.transition_type,
            "field_name": r.field_name,
            "previous_value": r.previous_value,
            "current_value": r.current_value,
            "detected_at": _iso(r.detected_at),
            "effective_at": _iso(r.effective_at),
            "confidence": r.confidence,
            "epistemic_status": r.epistemic_status,
            "detector_version": r.detector_version,
        }

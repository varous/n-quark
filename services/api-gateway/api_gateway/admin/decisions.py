"""Append-only resolution-decision store (governance). Immutable except explicit reversal metadata.

Idempotent on ``idempotency_key``: a retried/double-clicked submission returns the existing decision
rather than creating a duplicate (DECISION_ALREADY_APPLIED at the route layer)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from api_gateway.db.models import AdminResolutionDecision
from api_gateway.db.session import get_session


def _row(d: AdminResolutionDecision) -> dict[str, Any]:
    return {
        "id": d.id, "idempotency_key": d.idempotency_key, "candidate_id": d.candidate_id,
        "entity_type": d.entity_type, "source": d.source, "source_record_id": d.source_record_id,
        "source_handle": d.source_handle, "action": d.action,
        "previous_status": d.previous_status, "new_status": d.new_status,
        "previous_canonical_entity_id": d.previous_canonical_entity_id,
        "selected_canonical_entity_id": d.selected_canonical_entity_id,
        "created_canonical_entity_id": d.created_canonical_entity_id,
        "supersedes_decision_id": d.supersedes_decision_id,
        "actor_id": d.actor_id, "actor_role": d.actor_role, "reason_code": d.reason_code,
        "note": d.note, "impact_snapshot": d.impact_snapshot, "result": d.result,
        "request_id": d.request_id, "created_at": d.created_at.isoformat(),
        "reversed": d.reversed_at is not None,
        "reversed_at": d.reversed_at.isoformat() if d.reversed_at else None,
        "reversed_by": d.reversed_by, "reversed_by_decision_id": d.reversed_by_decision_id,
    }


class DecisionStore:
    def __init__(self, session_factory=None) -> None:
        self._Session = session_factory or get_session()

    def find_by_idempotency(self, key: str) -> dict[str, Any] | None:
        with self._Session() as s:
            d = s.execute(
                select(AdminResolutionDecision).where(AdminResolutionDecision.idempotency_key == key)
            ).scalar_one_or_none()
            return _row(d) if d else None

    def create(self, *, idempotency_key: str, action: str, actor_id: str, actor_role: str,
               request_id: str, **fields) -> dict[str, Any]:
        now = datetime.now(UTC)
        d = AdminResolutionDecision(
            id=uuid.uuid4().hex, idempotency_key=idempotency_key, action=action,
            actor_id=actor_id, actor_role=actor_role, request_id=request_id, created_at=now,
            **{k: v for k, v in fields.items()
               if k in AdminResolutionDecision.__table__.columns.keys()})  # noqa: SIM118
        with self._Session() as s, s.begin():
            s.add(d)
        return _row(d)

    def get(self, decision_id: str) -> dict[str, Any] | None:
        with self._Session() as s:
            d = s.get(AdminResolutionDecision, decision_id)
            return _row(d) if d else None

    def dependents(self, decision_id: str) -> list[str]:
        """Non-reversed decisions that build on this one (reference it via supersedes_decision_id)."""
        with self._Session() as s:
            rows = s.execute(
                select(AdminResolutionDecision.id).where(
                    AdminResolutionDecision.supersedes_decision_id == decision_id,
                    AdminResolutionDecision.reversed_at.is_(None))
            ).scalars().all()
            return list(rows)

    def mark_reversed(self, decision_id: str, *, by_actor: str, by_decision_id: str) -> None:
        with self._Session() as s, s.begin():
            d = s.get(AdminResolutionDecision, decision_id)
            if d is not None:
                d.reversed_at = datetime.now(UTC)
                d.reversed_by = by_actor
                d.reversed_by_decision_id = by_decision_id

    def list(self, *, action: str | None = None, candidate_id: str | None = None,
             entity_id: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        with self._Session() as s:
            stmt = select(AdminResolutionDecision)
            if action:
                stmt = stmt.where(AdminResolutionDecision.action == action)
            if candidate_id:
                stmt = stmt.where(AdminResolutionDecision.candidate_id == candidate_id)
            if entity_id:
                stmt = stmt.where(AdminResolutionDecision.selected_canonical_entity_id == entity_id)
            rows = s.execute(
                stmt.order_by(AdminResolutionDecision.created_at.desc()).offset(offset).limit(limit)
            ).scalars().all()
            return {"count": len(rows), "items": [_row(d) for d in rows]}

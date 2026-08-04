"""Admin audit store — records every operational/governance action (who/what/when/target/request-id).

Backed by the Alembic-managed gateway DB (``api_gateway.db``); a SQLite dev/test fallback is created from
metadata. Read-only page access is not audited. This is NOT a generic mutation endpoint — only
structured audit rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from api_gateway.db.models import AdminAuditRecord
from api_gateway.db.session import get_session


class AuditStore:
    def __init__(self, session_factory=None) -> None:
        self._Session = session_factory or get_session()

    def record(self, *, actor_id: str, actor_role: str, action: str, object_type: str,
               object_id: str, request_id: str, previous_value: Any = None,
               new_value: Any = None, reason: str | None = None) -> dict[str, Any]:
        now = datetime.now(UTC)
        rec = AdminAuditRecord(
            id=uuid.uuid4().hex, actor_id=actor_id, actor_role=actor_role, action=action,
            object_type=object_type, object_id=object_id, request_id=request_id,
            previous_value=previous_value, new_value=new_value, reason=reason, created_at=now)
        with self._Session() as s, s.begin():
            s.add(rec)
        return {"id": rec.id, "request_id": request_id, "created_at": now.isoformat()}

    def list(self, *, actor: str | None = None, action: str | None = None,
             object_type: str | None = None, object_id: str | None = None,
             request_id: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        with self._Session() as s:
            stmt = select(AdminAuditRecord)
            if actor:
                stmt = stmt.where(AdminAuditRecord.actor_id == actor)
            if action:
                stmt = stmt.where(AdminAuditRecord.action == action)
            if object_type:
                stmt = stmt.where(AdminAuditRecord.object_type == object_type)
            if object_id:
                stmt = stmt.where(AdminAuditRecord.object_id == object_id)
            if request_id:
                stmt = stmt.where(AdminAuditRecord.request_id == request_id)
            rows = s.execute(
                stmt.order_by(AdminAuditRecord.created_at.desc()).offset(offset).limit(limit)
            ).scalars().all()
            return {"count": len(rows), "items": [{
                "id": r.id, "actor_id": r.actor_id, "actor_role": r.actor_role, "action": r.action,
                "object_type": r.object_type, "object_id": r.object_id, "request_id": r.request_id,
                "previous_value": r.previous_value, "new_value": r.new_value,
                "reason": r.reason, "created_at": r.created_at.isoformat()} for r in rows]}

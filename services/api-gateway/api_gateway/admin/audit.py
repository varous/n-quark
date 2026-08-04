"""Admin audit store. Records every operational action (who/what/when/target/request-id).

The gateway has no Alembic yet, so the single additive table is created idempotently at startup
(check-first `create_all`); dropping the table is the reverse. Read-only page access is not audited
(no existing policy requires it). This is NOT a generic mutation endpoint — only structured audit rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, String, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import JSON

from api_gateway.config import settings


class Base(DeclarativeBase):
    pass


def _json_type() -> JSON | JSONB:
    return JSON().with_variant(JSONB, "postgresql")


class AdminAuditRecord(Base):
    __tablename__ = "admin_audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(24), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(600), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_value: Mapped[Any] = mapped_column(_json_type(), nullable=True)
    new_value: Mapped[Any] = mapped_column(_json_type(), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditStore:
    def __init__(self, url: str | None = None) -> None:
        db_url = url or settings.admin_audit_db_url or settings.postgres_url
        self._engine = self._make_engine(db_url)
        if self._engine is None:  # driver missing / DB unreachable -> local sqlite fallback
            self._engine = self._make_engine("sqlite:////tmp/nquark_admin_audit.db")
        Base.metadata.create_all(self._engine)
        self._Session = sessionmaker(bind=self._engine, expire_on_commit=False)

    @staticmethod
    def _make_engine(db_url: str):
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        try:
            engine = create_engine(db_url, connect_args=connect_args, pool_pre_ping=True)
            with engine.connect():
                pass
            return engine
        except Exception:  # noqa: BLE001 — fall back rather than break the admin surface
            return None

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

    def list(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        with self._Session() as s:
            rows = s.execute(
                select(AdminAuditRecord).order_by(AdminAuditRecord.created_at.desc())
                .offset(offset).limit(limit)
            ).scalars().all()
            return {"count": len(rows), "items": [{
                "id": r.id, "actor_id": r.actor_id, "actor_role": r.actor_role, "action": r.action,
                "object_type": r.object_type, "object_id": r.object_id, "request_id": r.request_id,
                "reason": r.reason, "created_at": r.created_at.isoformat()} for r in rows]}

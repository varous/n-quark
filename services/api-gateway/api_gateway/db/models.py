"""Gateway admin governance models: the audit log and the append-only resolution-decision record."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


def _json_type() -> JSON | JSONB:
    return JSON().with_variant(JSONB, "postgresql")


class AdminAuditRecord(Base):
    __tablename__ = "admin_audit_log"
    __table_args__ = (
        Index("ix_admin_audit_created", "created_at"),
        Index("ix_admin_audit_object", "object_type", "object_id"),
        Index("ix_admin_audit_request", "request_id"),
    )

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


class AdminResolutionDecision(Base):
    """Append-only governance record for a manual entity-resolution decision.

    Immutable except for explicit reversal metadata (``reversed_at``/``reversed_by``). Deduped on
    ``idempotency_key`` so a retried/double-clicked submission never creates a duplicate decision."""

    __tablename__ = "admin_resolution_decision"
    __table_args__ = (
        Index("uq_admin_decision_idem", "idempotency_key", unique=True),
        Index("ix_admin_decision_candidate", "candidate_id"),
        Index("ix_admin_decision_created", "created_at"),
        Index("ix_admin_decision_entity", "selected_canonical_entity_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False)
    candidate_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_handle: Mapped[str | None] = mapped_column(String(600), nullable=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    new_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    previous_canonical_entity_id: Mapped[str | None] = mapped_column(String(600), nullable=True)
    selected_canonical_entity_id: Mapped[str | None] = mapped_column(String(600), nullable=True)
    created_canonical_entity_id: Mapped[str | None] = mapped_column(String(600), nullable=True)
    supersedes_decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    note: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    impact_snapshot: Mapped[Any] = mapped_column(_json_type(), nullable=True)
    result: Mapped[Any] = mapped_column(_json_type(), nullable=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reversed_by_decision_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

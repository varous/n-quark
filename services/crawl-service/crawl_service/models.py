"""Scheduled-capture persistence (Phase 2).

Two tables, both owned by crawl-service:
- ``tracked_event`` — one operational-coverage record per (source, source_record_id): when it was
  last checked/seen, how many captures/states/transitions, and when the next capture is due.
- ``scheduled_capture_job`` — a due unit of work, lease-locked and idempotent on a capture window,
  so duplicate cron invocations / concurrent workers cannot double-process the same window.

Nothing here is graph or Shadow Ledger state; the scheduler calls signal-service over HTTP and the
Shadow Ledger remains the authority for commercial-state history.
"""

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


def _json_type() -> JSON | JSONB:
    return JSON().with_variant(JSONB, "postgresql")


class TrackedEvent(Base):
    __tablename__ = "tracked_event"
    __table_args__ = (
        Index("uq_tracked_event", "source", "source_record_id", unique=True),
        Index("ix_tracked_event_due", "next_capture_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_event_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    city: Mapped[str | None] = mapped_column(String(255), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    on_sale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # ACTIVE | POST_EVENT | STOPPED | NEEDS_REVIEW | CANCELLED
    tracking_status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    first_tracked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_record_present_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_state_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_capture_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cadence_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority_reason: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_absences: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    capture_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    distinct_state_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transition_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_capture_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScheduledCaptureJob(Base):
    __tablename__ = "scheduled_capture_job"
    __table_args__ = (
        # One job per (source, source_record_id, capture window) — idempotent against duplicate cron.
        Index("uq_capture_job_window", "dedup_key", unique=True),
        Index("ix_capture_job_claimable", "status", "scheduled_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(700), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_event_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # PENDING | RUNNING | SUCCEEDED | FAILED_RETRYABLE | FAILED_TERMINAL | SKIPPED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_capture_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lock_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

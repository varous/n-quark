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
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String
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
    # --- Phase 2.1: enrichment-derived scheduling metadata (additive/nullable) ---
    region_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    event_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_on_sale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_ticket_state_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enrichment_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    enrichment_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)


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


# --------------------------------------------------------------------------- Phase 2.1 enrichment
class EnrichmentCandidate(Base):
    """A single field-value candidate extracted from one evidence surface, with full provenance.

    Append-only per (event, field, content_hash): re-observing identical evidence is idempotent;
    changed evidence yields a new ACTIVE candidate and supersedes the old one.
    """

    __tablename__ = "enrichment_candidate"
    __table_args__ = (
        Index("ix_enrichment_candidate_event_field", "canonical_event_id", "field_name"),
        Index("uq_enrichment_candidate_hash", "canonical_event_id", "field_name", "content_hash", unique=True),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_value: Mapped[Any] = mapped_column(_json_type(), nullable=True)
    normalized_value: Mapped[Any] = mapped_column(_json_type(), nullable=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # Phase 2.2 provenance: surface (finer than source_type), source family, independence group.
    surface: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_family: Mapped[str | None] = mapped_column(String(40), nullable=True)
    independence_group: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    extraction_method: Mapped[str] = mapped_column(String(40), nullable=False)
    epistemic_status: Mapped[str] = mapped_column(String(40), nullable=False, default="observed_public_state")
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventFieldResolution(Base):
    """The current + historical resolution of one canonical event field from its candidates.

    Exactly one CURRENT row per (event, field); prior resolutions are preserved (is_current=False)
    so reschedules and revisions stay auditable — earlier resolutions are never overwritten."""

    __tablename__ = "event_field_resolution"
    __table_args__ = (
        Index("ix_event_field_resolution", "canonical_event_id", "field_name", "is_current"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_value: Mapped[Any] = mapped_column(_json_type(), nullable=True)
    resolution_method: Mapped[str] = mapped_column(String(40), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="AUTO_RESOLVED")
    reason_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    supporting_candidate_ids: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    contradicting_candidate_ids: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    resolver_version: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EnrichmentRun(Base):
    """Audit of one live public-page enrichment pilot run (Phase 2.2). Immutable once completed."""

    __tablename__ = "enrichment_run"
    __table_args__ = (Index("ix_enrichment_run_started", "started_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    surface: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    events_selected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_attempted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_retrieved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidates_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolutions_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conflicts_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parser_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    config_snapshot: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EventMatchCandidate(Base):
    """A cross-platform match candidate between two source listings (Phase 3).

    Deduped on the unordered canonical-event pair. Accepting a match links source records to one
    canonical event WITHOUT collapsing either source's records/history (truth preserved)."""

    __tablename__ = "event_match_candidate"
    __table_args__ = (
        Index("uq_event_match_pair", "pair_key", unique=True),
        Index("ix_event_match_status", "match_status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pair_key: Mapped[str] = mapped_column(String(1050), nullable=False)
    left_source: Mapped[str] = mapped_column(String(64), nullable=False)
    left_source_record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    left_canonical_event_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    right_source: Mapped[str] = mapped_column(String(64), nullable=False)
    right_source_record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    right_canonical_event_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    match_status: Mapped[str] = mapped_column(String(24), nullable=False)   # MATCHED|POSSIBLE_MATCH|CONFLICT|NOT_MATCHED
    match_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    component_scores: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    supporting_signals: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    contradicting_signals: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    reason_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    matcher_version: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(String(24), nullable=False, default="AUTO")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ------------------------------------------------------------------- Phase 3.1 entity resolution
class EntityResolutionCandidate(Base):
    """One source-event entity mention (artist/venue/organizer/series) and its resolution decision.

    The audit + queue record: it holds the raw + normalized evidence, the chosen canonical entity (or
    none), the status/score/reason, and the supporting/contradicting signals. Deduped on
    (entity_type, source, source_record_id, source_entity_handle) so re-running updates in place."""

    __tablename__ = "entity_resolution_candidate"
    __table_args__ = (
        Index("uq_entity_res_candidate", "entity_type", "source", "source_record_id",
              "source_entity_handle", unique=True),
        Index("ix_entity_res_status", "entity_type", "resolution_status"),
        Index("ix_entity_res_canonical", "candidate_canonical_entity_id"),
        Index("ix_entity_res_name", "entity_type", "normalized_name"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)  # ARTIST|VENUE|ORGANIZER|EVENT_SERIES
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_event_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_entity_handle: Mapped[str] = mapped_column(String(600), nullable=False)
    raw_name: Mapped[str] = mapped_column(String(600), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(600), nullable=False)
    candidate_canonical_entity_id: Mapped[str | None] = mapped_column(String(600), nullable=True)
    match_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    resolution_status: Mapped[str] = mapped_column(String(24), nullable=False)  # RESOLVED|POSSIBLE_MATCH|AMBIGUOUS|UNRESOLVED|REJECTED
    reason_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    supporting_signals: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    contradicting_signals: Mapped[list] = mapped_column(_json_type(), nullable=False, default=list)
    evidence: Mapped[dict] = mapped_column(_json_type(), nullable=False, default=dict)
    resolver_version: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntitySourceHandle(Base):
    """A source-specific identity that maps to a canonical entity (the alias registry).

    A canonical entity accretes several source handles over time (Boshow performer text, District
    profile handle, …); a handle is strong future resolution evidence. Unique per (source, handle)."""

    __tablename__ = "entity_source_handle"
    __table_args__ = (
        Index("uq_entity_source_handle", "source", "source_entity_handle", unique=True),
        Index("ix_entity_handle_canonical", "canonical_entity_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_entity_handle: Mapped[str] = mapped_column(String(600), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    canonical_entity_id: Mapped[str] = mapped_column(String(600), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    resolution_method: Mapped[str | None] = mapped_column(String(48), nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EntityResolutionHistory(Base):
    """Append-only status-change log for an entity resolution candidate (auditable resolution history).

    Records only genuine status/canonical transitions (e.g. a venue UNRESOLVED -> RESOLVED once address
    evidence arrives); source evidence itself is never rewritten."""

    __tablename__ = "entity_resolution_history"
    __table_args__ = (Index("ix_entity_res_history", "candidate_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    new_status: Mapped[str] = mapped_column(String(24), nullable=False)
    previous_canonical_entity_id: Mapped[str | None] = mapped_column(String(600), nullable=True)
    new_canonical_entity_id: Mapped[str | None] = mapped_column(String(600), nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    resolver_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

"""Demand-observation persistence (Phase 5A) — a ledger SEPARATE from the event Shadow Ledger.

Four tables, all owned by artist-intelligence-service:

- ``artist_external_identity`` — a canonical artist's identity on an external platform (a YouTube
  channel, a Trends search-term/topic). Attaches to an existing canonical artist; never creates one.
- ``artist_demand_observation`` — a temporal/geographic demand fact from a provider, with its own
  provenance + epistemic status. Append-only and idempotent on ``observation_key``: re-ingesting the
  same provider fact does not duplicate a logical observation. History is preserved, never overwritten.
- ``provider_quota_day`` — per-provider/day request + quota accounting (search vs non-search units).
- ``demand_refresh_job`` — a lease-locked, idempotent, restart-safe unit of refresh work.

None of this is event/graph/Shadow-Ledger state. YouTube/Trends metrics never enter the event ledger.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


def _json_type() -> JSON | JSONB:
    return JSON().with_variant(JSONB, "postgresql")


class ArtistExternalIdentity(Base):
    """A canonical artist's identity on an external platform. One canonical artist may have many.

    Provider IDs are stable identifiers where available (channel id, topic mid). Name search alone
    never becomes silent canonical truth: ambiguous identities stay ``AMBIGUOUS``/``UNRESOLVED``.
    Unique per (provider, identity_type, provider_id) so re-resolution updates in place."""

    __tablename__ = "artist_external_identity"
    __table_args__ = (
        Index("uq_artist_external_identity", "provider", "identity_type", "provider_id", unique=True),
        Index("ix_aei_canonical", "canonical_artist_id"),
        Index("ix_aei_provider_status", "provider", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_artist_id: Mapped[str] = mapped_column(String(512), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)        # YOUTUBE | GOOGLE_TRENDS
    identity_type: Mapped[str] = mapped_column(String(32), nullable=False)   # CHANNEL_ID|HANDLE|SEARCH_TERM|TOPIC_ID
    provider_id: Mapped[str] = mapped_column(String(600), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(600), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # RESOLVED | AMBIGUOUS | UNRESOLVED | REJECTED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="UNRESOLVED")
    resolution_method: Mapped[str | None] = mapped_column(String(48), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    identity_metadata: Mapped[dict[str, Any]] = mapped_column("metadata_json", _json_type(),
                                                              nullable=False, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column("provenance_json", _json_type(),
                                                       nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtistDemandObservation(Base):
    """One temporal/geographic demand fact from a provider. Append-only; idempotent on observation_key.

    ``evidence_status`` records provider transformation honestly (a sampled/normalized value is NOT a
    raw observed fact). Google Trends values are RELATIVE search interest, never absolute volume."""

    __tablename__ = "artist_demand_observation"
    __table_args__ = (
        Index("uq_demand_observation_key", "observation_key", unique=True),
        Index("ix_ado_artist_metric", "canonical_artist_id", "provider", "metric"),
        Index("ix_ado_artist_scope", "canonical_artist_id", "scope_type", "scope_id"),
        Index("ix_ado_observed_at", "observed_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_artist_id: Mapped[str] = mapped_column(String(512), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_identity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(48), nullable=True)
    # GLOBAL | COUNTRY | REGION | SUBREGION | CONTENT
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False, default="GLOBAL")
    scope_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scope_label: Mapped[str | None] = mapped_column(String(512), nullable=True)
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # DIRECT_PROVIDER_VALUE | PROVIDER_REPORTED | PROVIDER_SAMPLED | PROVIDER_NORMALIZED |
    # IMPORTED_PROVIDER_EXPORT | DERIVED | SEARCH_TERM_BASED | TOPIC_BASED | UNKNOWN
    evidence_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    provenance: Mapped[dict[str, Any]] = mapped_column("provenance_json", _json_type(),
                                                       nullable=False, default=dict)
    observation_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderQuotaDay(Base):
    """Per-provider/day request + quota accounting. Search and non-search are tracked separately so
    identity discovery (expensive: 100 units/search) is visibly disciplined against measurement."""

    __tablename__ = "provider_quota_day"
    __table_args__ = (
        Index("uq_provider_quota_day", "provider", "quota_date", unique=True),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    quota_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    search_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    non_search_quota_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    search_quota_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DemandRefreshJob(Base):
    """A due unit of demand-refresh work — lease-locked and idempotent on a refresh window, so
    duplicate loop ticks / concurrent workers cannot double-process the same window.

    All durable state lives here, so a process restart just resumes idempotently (no lost jobs, no
    duplicated refreshes)."""

    __tablename__ = "demand_refresh_job"
    __table_args__ = (
        Index("uq_demand_refresh_window", "dedup_key", unique=True),
        Index("ix_demand_job_claimable", "status", "scheduled_at"),
        Index("ix_demand_job_artist", "canonical_artist_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dedup_key: Mapped[str] = mapped_column(String(700), nullable=False)
    canonical_artist_id: Mapped[str] = mapped_column(String(512), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # YOUTUBE_CHANNEL_SNAPSHOT | YOUTUBE_VIDEO_SNAPSHOT | GOOGLE_TRENDS_REFRESH
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    external_identity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # PENDING | RUNNING | SUCCEEDED | FAILED_RETRYABLE | FAILED_TERMINAL | SKIPPED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    result_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lock_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

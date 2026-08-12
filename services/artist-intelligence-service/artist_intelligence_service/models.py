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
    # Acquisition priority class (Phase 5A.3): lower = more urgent (P0=0 … P4=40). Ordered before
    # scheduled_at when claiming, so scarce quota goes to the most irreplaceable work first. This is an
    # operational priority, NOT an artist-value/popularity score.
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    detail: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtistCandidate(Base):
    """Phase 5A.3 — a discovered artist candidate, BEFORE any canonical decision.

    Decouples the artist universe from ticketing coverage: candidates arrive from multiple discovery
    surfaces (event-derived, YouTube search/ecosystem, future ingestion seams) and only become linked to
    a canonical ARTIST through the existing entity-resolution semantics. A candidate is NEVER a canonical
    artist and NEVER creates one. Idempotent on (discovery_source, discovery_source_id): repeated
    discovery of the same source id merges evidence instead of exploding rows."""

    __tablename__ = "artist_candidate"
    __table_args__ = (
        Index("uq_artist_candidate_source", "discovery_source", "discovery_source_id", unique=True),
        Index("ix_artist_candidate_status", "status"),
        Index("ix_artist_candidate_canonical", "canonical_artist_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(600), nullable=False)
    discovery_source: Mapped[str] = mapped_column(String(48), nullable=False)      # EVENT|YOUTUBE_SEARCH|YOUTUBE_ECOSYSTEM|IMPORT
    discovery_source_id: Mapped[str] = mapped_column(String(600), nullable=False)  # stable id within the source
    discovery_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    country_hint: Mapped[str | None] = mapped_column(String(16), nullable=True)
    region_hint: Mapped[str | None] = mapped_column(String(120), nullable=True)
    language_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    genre_hint: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # NEW | RESOLUTION_PENDING | RESOLVED | AMBIGUOUS | REJECTED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="NEW")
    canonical_artist_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    evidence_refs: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column("evidence_json", _json_type(), nullable=False, default=dict)
    provenance: Mapped[dict[str, Any]] = mapped_column("provenance_json", _json_type(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtistMarketEvidence(Base):
    """Phase 5A.3 — explicit, provenance-bearing evidence of an artist's relationship to the India
    market. Evidence CLASSIFICATIONS, never a quality/relevance score:

    - ``CONFIRMED_LIVE_INDIA`` — observed Indian ticketed event / lineup / venue calendar / promoter /
      tour / authorized feed (the artist has demonstrably played, or is booked to play, India);
    - ``INDIA_DEMAND_OBSERVED`` — India/sub-region demand evidence (Trends) — does NOT imply performing;
    - ``INDIA_MARKET_CANDIDATE`` — market-relevant candidate lacking stronger India evidence.

    Append-only + idempotent on (canonical_artist_id, evidence_class, source, source_ref)."""

    __tablename__ = "artist_market_evidence"
    __table_args__ = (
        Index("uq_artist_market_evidence", "canonical_artist_id", "evidence_class", "source",
              "source_ref", unique=True),
        Index("ix_ame_artist", "canonical_artist_id"),
        Index("ix_ame_class", "evidence_class"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_artist_id: Mapped[str] = mapped_column(String(512), nullable=False)
    country: Mapped[str] = mapped_column(String(16), nullable=False, default="IN")
    evidence_class: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(48), nullable=False)          # EVENT|GOOGLE_TRENDS|YOUTUBE_DISCOVERY|IMPORT
    source_ref: Mapped[str] = mapped_column(String(600), nullable=False)     # event id / region / query
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column("provenance_json", _json_type(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class YouTubeVideo(Base):
    """Phase 5A.3 — the video REGISTRY for a resolved channel, separate from time-series observations.

    Stable, mostly-static metadata captured once (title, published_at, duration, category, live status);
    demand metrics (views/likes/comments over time) live in ``artist_demand_observation`` keyed by hour.
    Idempotent on video_id."""

    __tablename__ = "youtube_video"
    __table_args__ = (
        Index("ix_ytv_channel", "channel_id"),
        Index("ix_ytv_artist", "canonical_artist_id"),
        Index("ix_ytv_published", "published_at"),
        Index("ix_ytv_relationship", "canonical_artist_id", "relationship_type"),
    )

    video_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_artist_id: Mapped[str] = mapped_column(String(512), nullable=False)
    external_identity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    live_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tracking_status: Mapped[str] = mapped_column(String(24), nullable=False, default="ACTIVE")  # ACTIVE|DORMANT
    # Phase 5B.2 — how this video relates to the artist. OWNED_CONTENT = published by the artist's verified
    # channel (channel-ownership only; NOT a claim of musical relevance). ECOSYSTEM_CONTENT = a third-party
    # channel that plausibly features/concerns the artist (relevance carried in metadata_json, not assumed).
    relationship_type: Mapped[str] = mapped_column(String(24), nullable=False, default="OWNED_CONTENT")
    # AVAILABLE | UNAVAILABLE | NOT_FOUND — a KNOWN video's provider availability (5A.1a discipline: a
    # transient provider failure never becomes NOT_FOUND). History is never deleted on unavailability.
    availability_state: Mapped[str] = mapped_column(String(16), nullable=False, default="AVAILABLE")
    discovery_method: Mapped[str | None] = mapped_column(String(48), nullable=True)  # CHANNEL_UPLOADS|ECOSYSTEM_SEARCH|…
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ArtistWatchTarget(Base):
    """Phase 5B.1 — an operator's instruction to *attempt and maintain* observation of an artist.

    A watch target is NOT a canonical artist and NEVER creates one: it is a durable research
    instruction that the system tries to resolve (link to an existing canonical, or promote through the
    existing candidate/evidence rules) and, once resolved, onboards into the existing demand pipeline.

    This is the one narrow operator-writable surface (RESEARCH CONFIGURATION): operators may create /
    prioritise / pause / resume targets. It never mutates canonical entities, observations, graph nodes,
    provider observations, resolution outcomes, event state, or historical evidence — those stay owned by
    the entity/graph architecture and the append-only demand ledger.

    Idempotent on ``dedup_key`` so the same canonical artist / provider identity / name is never
    watched twice (``canonical:<id>`` › ``yt:<channel_id>`` › ``name:<normalized>``)."""

    __tablename__ = "artist_watch_target"
    __table_args__ = (
        Index("uq_artist_watch_target_dedup", "dedup_key", unique=True),
        Index("ix_awt_status", "status"),
        Index("ix_awt_canonical", "canonical_artist_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(600), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(700), nullable=False)
    canonical_artist_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # the raw pasted hint (channel/video URL, handle, or channel id) exactly as the operator gave it
    youtube_hint: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # the provider-VERIFIED channel id (channels.list), set only after authoritative verification
    youtube_channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(48), nullable=False, default="OPERATOR")
    reason: Mapped[str | None] = mapped_column(String(600), nullable=True)
    # operational acquisition priority (P-class, lower = more urgent); NOT a popularity/value score
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    # NEW | RESOLUTION_PENDING | WATCHING | AMBIGUOUS | REJECTED | PAUSED
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="NEW")
    resolution_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detail: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    created_by: Mapped[str] = mapped_column(String(320), nullable=False)
    last_resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderQuotaBucketDay(Base):
    """Phase 5A.3 — per-bucket daily quota accounting (SEARCH | GENERAL_READ | VIDEO_STATS_BATCH).

    YouTube's real quota model prices calls very differently by endpoint; collapsing everything into one
    synthetic pool hid where budget goes. ``quota_date`` follows the provider's actual reset timezone
    (configurable), not UTC. The legacy per-day aggregate (``provider_quota_day``) is still written for
    back-compat; this table is the granular truth used for allocation + reserve enforcement."""

    __tablename__ = "provider_quota_bucket_day"
    __table_args__ = (
        Index("uq_provider_quota_bucket_day", "provider", "quota_date", "bucket", unique=True),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    quota_date: Mapped[datetime] = mapped_column(Date, nullable=False)   # in the provider's reset tz
    bucket: Mapped[str] = mapped_column(String(32), nullable=False)
    requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

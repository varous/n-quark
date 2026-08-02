"""SQLAlchemy models for the Postgres-backed graph store.

The knowledge graph is intentionally shallow — nodes are canonical entities and edges are
directional, deduped relationships. Two tables cover it; JSON properties keep the schema
type-agnostic (the same node record holds an event, venue, artist, or region). JSONB on
Postgres, plain JSON on SQLite (used by the unit tests), via the shared variant helper.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, PrimaryKeyConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


def _json_type() -> JSON | JSONB:
    return JSON().with_variant(JSONB, "postgresql")


class GraphNodeRecord(Base):
    """A canonical entity node. ``id`` is the canonical id (e.g. ``event:diljit-kolkata``)."""

    __tablename__ = "graph_nodes"
    __table_args__ = (Index("ix_graph_nodes_type", "type"),)

    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    type: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    # ``updated_at`` is mirrored inside properties (the feed's incremental cursor reads it
    # from there, matching the in-memory / Neo4j stores) and kept as a column for indexing.
    properties: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphEdgeRecord(Base):
    """A directional relationship. The (source, relationship, target) triple is the identity,
    so re-projecting the same edge MERGEs rather than duplicating (idempotent projection)."""

    __tablename__ = "graph_edges"
    __table_args__ = (
        PrimaryKeyConstraint("source", "relationship", "target", name="pk_graph_edges"),
        Index("ix_graph_edges_source", "source"),
        Index("ix_graph_edges_target", "target"),
    )

    source: Mapped[str] = mapped_column(String(512), nullable=False)
    relationship: Mapped[str] = mapped_column(String(128), nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)


# --------------------------------------------------------------------------- Shadow Ledger
# Phase 1 — Minimum Viable Shadow Ledger. Relational (not graph nodes), keyed by the canonical
# event id — per ADR-0002/0008 the GraphStore abstraction is left untouched and transition history
# is exposed through the service layer. Both tables are append-only; see docs/adr/.


class ShadowStateRecord(Base):
    """One captured, normalized public commercial state of an event at a point in time.

    Every genuinely-distinct state is preserved (append-only). Consecutive identical states are
    suppressed upstream by state-hash comparison, so this table never stores no-op duplicates.
    ``normalized_state`` is the deterministic, volatile-field-free state the hash is computed over.
    """

    __tablename__ = "shadow_state"
    __table_args__ = (
        Index("ix_shadow_state_event", "canonical_event_id"),
        Index("ix_shadow_state_lookup", "canonical_event_id", "source_id", "source_record_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    observation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # state_hash == the effective-state hash (hash of the merged commercial state). Kept under the
    # original column name for backward compatibility; capture_hash (below) is what THIS capture saw.
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_state: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False)
    epistemic_status: Mapped[str] = mapped_column(String(64), nullable=False, default="observed_public_state")
    provenance: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    previous_state_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    detector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # --- Phase 1.1: capture completeness + integrity (all additive/nullable) ---
    snapshot_completeness: Mapped[str | None] = mapped_column(String(16), nullable=True)
    capture_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    field_status: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    capture_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    out_of_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    absence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ShadowTransitionRecord(Base):
    """An immutable, explicit transition between two states (or a lifecycle event).

    ``dedup_key`` = f"{to_state_id}:{transition_type}:{field_name or ''}" carries a unique index so
    reprocessing the same detection never inserts a duplicate transition.
    """

    __tablename__ = "shadow_transition"
    __table_args__ = (
        Index("ix_shadow_transition_event", "canonical_event_id"),
        Index("uq_shadow_transition_dedup", "dedup_key", unique=True),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_state_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_state_id: Mapped[str] = mapped_column(String(64), nullable=False)
    transition_type: Mapped[str] = mapped_column(String(64), nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_value: Mapped[Any] = mapped_column(_json_type(), nullable=True)
    current_value: Mapped[Any] = mapped_column(_json_type(), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.9)
    epistemic_status: Mapped[str] = mapped_column(String(64), nullable=False, default="observed_public_state")
    detector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(700), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

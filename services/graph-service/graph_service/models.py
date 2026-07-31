"""SQLAlchemy models for the Postgres-backed graph store.

The knowledge graph is intentionally shallow — nodes are canonical entities and edges are
directional, deduped relationships. Two tables cover it; JSON properties keep the schema
type-agnostic (the same node record holds an event, venue, artist, or region). JSONB on
Postgres, plain JSON on SQLite (used by the unit tests), via the shared variant helper.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, PrimaryKeyConstraint, String
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

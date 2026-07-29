import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


def _json_type() -> JSON | JSONB:
    return JSON().with_variant(JSONB, "postgresql")


class ObservationRecord(Base):
    """Append-only observation storage. Rows are never updated or deleted."""

    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_observations_entity_timestamp", "entity", "timestamp"),
        Index("ix_observations_source_timestamp", "source", "timestamp"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    entity: Mapped[str] = mapped_column(String(512), nullable=False)
    attribute: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[Any] = mapped_column(_json_type(), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(_json_type(), nullable=False, default=dict)
    observation_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        _json_type(),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="Server ingest time; distinct from observation timestamp.",
    )

    def __repr__(self) -> str:
        return f"ObservationRecord(id={self.id!s}, entity={self.entity!r}, attribute={self.attribute!r})"

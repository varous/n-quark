from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


def _json_type() -> JSON | JSONB:
    return JSON().with_variant(JSONB, "postgresql")


class EntityRecord(Base):
    """Canonical entity registry."""

    __tablename__ = "entities"

    id: Mapped[str] = mapped_column(String(512), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    entity_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        _json_type(),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    aliases: Mapped[list["EntityAliasRecord"]] = relationship(
        back_populates="entity",
        cascade="all, delete-orphan",
    )


class EntityAliasRecord(Base):
    """Maps external references (e.g. artist:spotify:id) to canonical entities."""

    __tablename__ = "entity_aliases"
    __table_args__ = (Index("ix_entity_aliases_canonical_id", "canonical_id"),)

    alias_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    canonical_id: Mapped[str] = mapped_column(
        String(512),
        ForeignKey("entities.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    entity: Mapped[EntityRecord] = relationship(back_populates="aliases")

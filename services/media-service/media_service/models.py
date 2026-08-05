"""Creative-asset observation persistence (Phase 4B), all owned by media-service.

- ``media_asset`` — one row per distinct *content* (SHA-256). Source-independent identity.
- ``media_observation`` — one row per (event, source, role, url) sighting, source-specific + provenanced.
- ``event_media_state`` — the current creative per (canonical_event_id, source, asset_role).
- ``media_transition`` — an append-only, source-specific creative-change history (kept separate from
  the graph Shadow Ledger vocabulary).

media-service does NOT own canonical event identity or general event state; it only observes creatives.
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid4().hex


class MediaAsset(Base):
    __tablename__ = "media_asset"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    fetch_status: Mapped[str] = mapped_column(String(32), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class MediaObservation(Base):
    __tablename__ = "media_observation"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    canonical_event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_record_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    asset_role: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    normalized_url: Mapped[str] = mapped_column(String(2048), nullable=False, index=True)
    media_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    fetch_status: Mapped[str] = mapped_column(String(32), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # idempotency: one row per (event, source, role, url, observed_at)
        UniqueConstraint("canonical_event_id", "source", "asset_role", "normalized_url", "observed_at",
                         name="uq_media_observation_window"),
    )


class EventMediaState(Base):
    __tablename__ = "event_media_state"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    canonical_event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_role: Mapped[str] = mapped_column(String(32), nullable=False)
    current_media_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_observation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_normalized_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    present: Mapped[bool] = mapped_column(nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("canonical_event_id", "source", "asset_role", name="uq_event_media_state"),
    )


class MediaTransition(Base):
    __tablename__ = "media_transition"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    canonical_event_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_role: Mapped[str] = mapped_column(String(32), nullable=False)
    transition_type: Mapped[str] = mapped_column(String(48), nullable=False)
    from_media_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_media_asset_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    from_normalized_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    to_normalized_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    observation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    out_of_order: Mapped[bool] = mapped_column(nullable=False, default=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

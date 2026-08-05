"""Phase 4B — creative-asset observation tables

Revision ID: 001_media
Revises:
Create Date: 2026-08-05

Additive and reversible: four new tables owned by media-service. No existing table is touched.
"""
import sqlalchemy as sa

from alembic import op

revision = "001_media"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_asset",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("perceptual_hash", sa.String(length=64), nullable=True),
        sa.Column("mime_type", sa.String(length=64), nullable=True),
        sa.Column("byte_size", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("image_format", sa.String(length=16), nullable=True),
        sa.Column("storage_key", sa.String(length=128), nullable=True),
        sa.Column("fetch_status", sa.String(length=32), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("content_sha256", name="uq_media_asset_sha256"),
    )
    op.create_index("ix_media_asset_content_sha256", "media_asset", ["content_sha256"])
    op.create_index("ix_media_asset_perceptual_hash", "media_asset", ["perceptual_hash"])

    op.create_table(
        "media_observation",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("canonical_event_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=True),
        sa.Column("asset_role", sa.String(length=32), nullable=False),
        sa.Column("asset_url", sa.String(length=2048), nullable=False),
        sa.Column("normalized_url", sa.String(length=2048), nullable=False),
        sa.Column("media_asset_id", sa.String(length=32), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetch_status", sa.String(length=32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("canonical_event_id", "source", "asset_role", "normalized_url",
                            "observed_at", name="uq_media_observation_window"),
    )
    op.create_index("ix_media_observation_canonical_event_id", "media_observation", ["canonical_event_id"])
    op.create_index("ix_media_observation_source", "media_observation", ["source"])
    op.create_index("ix_media_observation_normalized_url", "media_observation", ["normalized_url"])
    op.create_index("ix_media_observation_media_asset_id", "media_observation", ["media_asset_id"])
    op.create_index("ix_media_observation_observed_at", "media_observation", ["observed_at"])

    op.create_table(
        "event_media_state",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("canonical_event_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("asset_role", sa.String(length=32), nullable=False),
        sa.Column("current_media_asset_id", sa.String(length=32), nullable=True),
        sa.Column("current_observation_id", sa.String(length=32), nullable=True),
        sa.Column("current_normalized_url", sa.String(length=2048), nullable=True),
        sa.Column("present", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("canonical_event_id", "source", "asset_role", name="uq_event_media_state"),
    )
    op.create_index("ix_event_media_state_canonical_event_id", "event_media_state", ["canonical_event_id"])

    op.create_table(
        "media_transition",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("canonical_event_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("asset_role", sa.String(length=32), nullable=False),
        sa.Column("transition_type", sa.String(length=48), nullable=False),
        sa.Column("from_media_asset_id", sa.String(length=32), nullable=True),
        sa.Column("to_media_asset_id", sa.String(length=32), nullable=True),
        sa.Column("from_normalized_url", sa.String(length=2048), nullable=True),
        sa.Column("to_normalized_url", sa.String(length=2048), nullable=True),
        sa.Column("observation_id", sa.String(length=32), nullable=True),
        sa.Column("out_of_order", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_media_transition_canonical_event_id", "media_transition", ["canonical_event_id"])
    op.create_index("ix_media_transition_detected_at", "media_transition", ["detected_at"])


def downgrade() -> None:
    op.drop_table("media_transition")
    op.drop_table("event_media_state")
    op.drop_table("media_observation")
    op.drop_table("media_asset")

"""Phase 5A.3 — Indian artist universe & demand saturation.

Additive and reversible. Adds the candidate layer, India market-presence evidence, the YouTube video
registry, per-bucket quota accounting, and a job priority column. Touches no existing rows destructively
and does NOT retroactively rewrite the daily demand observations (hourly precision applies only to new
records, via a finer observation_key bucket).

Revision ID: 002_artist_universe
Revises: 001_demand_tables
Create Date: 2026-08-08
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "002_artist_universe"
down_revision = "001_demand_tables"
branch_labels = None
depends_on = None


def _json():
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "artist_candidate",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(600), nullable=False),
        sa.Column("discovery_source", sa.String(48), nullable=False),
        sa.Column("discovery_source_id", sa.String(600), nullable=False),
        sa.Column("discovery_method", sa.String(64), nullable=True),
        sa.Column("source_url", sa.String(1024), nullable=True),
        sa.Column("country_hint", sa.String(16), nullable=True),
        sa.Column("region_hint", sa.String(120), nullable=True),
        sa.Column("language_hint", sa.String(64), nullable=True),
        sa.Column("genre_hint", sa.String(120), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="NEW"),
        sa.Column("canonical_artist_id", sa.String(512), nullable=True),
        sa.Column("evidence_refs", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_json", _json(), nullable=False),
        sa.Column("provenance_json", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_artist_candidate_source", "artist_candidate",
                    ["discovery_source", "discovery_source_id"], unique=True)
    op.create_index("ix_artist_candidate_status", "artist_candidate", ["status"])
    op.create_index("ix_artist_candidate_canonical", "artist_candidate", ["canonical_artist_id"])

    op.create_table(
        "artist_market_evidence",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_artist_id", sa.String(512), nullable=False),
        sa.Column("country", sa.String(16), nullable=False, server_default="IN"),
        sa.Column("evidence_class", sa.String(32), nullable=False),
        sa.Column("source", sa.String(48), nullable=False),
        sa.Column("source_ref", sa.String(600), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance_json", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_artist_market_evidence", "artist_market_evidence",
                    ["canonical_artist_id", "evidence_class", "source", "source_ref"], unique=True)
    op.create_index("ix_ame_artist", "artist_market_evidence", ["canonical_artist_id"])
    op.create_index("ix_ame_class", "artist_market_evidence", ["evidence_class"])

    op.create_table(
        "youtube_video",
        sa.Column("video_id", sa.String(32), primary_key=True),
        sa.Column("channel_id", sa.String(64), nullable=False),
        sa.Column("canonical_artist_id", sa.String(512), nullable=False),
        sa.Column("external_identity_id", sa.String(64), nullable=True),
        sa.Column("title", sa.String(1024), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("live_status", sa.String(32), nullable=True),
        sa.Column("tracking_status", sa.String(24), nullable=False, server_default="ACTIVE"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ytv_channel", "youtube_video", ["channel_id"])
    op.create_index("ix_ytv_artist", "youtube_video", ["canonical_artist_id"])
    op.create_index("ix_ytv_published", "youtube_video", ["published_at"])

    op.create_table(
        "provider_quota_bucket_day",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("quota_date", sa.Date(), nullable=False),
        sa.Column("bucket", sa.String(32), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quota_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_provider_quota_bucket_day", "provider_quota_bucket_day",
                    ["provider", "quota_date", "bucket"], unique=True)

    # additive job priority (P0=0 … P4=40); existing rows default to the lowest urgency.
    op.add_column("demand_refresh_job",
                  sa.Column("priority", sa.Integer(), nullable=False, server_default="40"))
    op.create_index("ix_demand_job_priority", "demand_refresh_job", ["priority", "scheduled_at"])


def downgrade() -> None:
    op.drop_index("ix_demand_job_priority", table_name="demand_refresh_job")
    op.drop_column("demand_refresh_job", "priority")
    op.drop_table("provider_quota_bucket_day")
    op.drop_table("youtube_video")
    op.drop_table("artist_market_evidence")
    op.drop_table("artist_candidate")

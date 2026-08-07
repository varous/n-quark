"""Phase 5A — demand-observation ledger (separate from the event Shadow Ledger).

Additive and reversible: creates four new tables owned by artist-intelligence-service. It does not
touch any event/graph/Shadow-Ledger table.

Revision ID: 001_demand_tables
Revises:
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "001_demand_tables"
down_revision = None
branch_labels = None
depends_on = None


def _json():
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "artist_external_identity",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_artist_id", sa.String(512), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("identity_type", sa.String(32), nullable=False),
        sa.Column("provider_id", sa.String(600), nullable=False),
        sa.Column("display_name", sa.String(600), nullable=True),
        sa.Column("canonical_url", sa.String(1024), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="UNRESOLVED"),
        sa.Column("resolution_method", sa.String(48), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", _json(), nullable=False),
        sa.Column("provenance_json", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_artist_external_identity", "artist_external_identity",
                    ["provider", "identity_type", "provider_id"], unique=True)
    op.create_index("ix_aei_canonical", "artist_external_identity", ["canonical_artist_id"])
    op.create_index("ix_aei_provider_status", "artist_external_identity", ["provider", "status"])

    op.create_table(
        "artist_demand_observation",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_artist_id", sa.String(512), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("external_identity_id", sa.String(64), nullable=True),
        sa.Column("metric", sa.String(64), nullable=False),
        sa.Column("value_numeric", sa.Float(), nullable=True),
        sa.Column("value_text", sa.String(1024), nullable=True),
        sa.Column("unit", sa.String(48), nullable=True),
        sa.Column("scope_type", sa.String(24), nullable=False, server_default="GLOBAL"),
        sa.Column("scope_id", sa.String(255), nullable=True),
        sa.Column("scope_label", sa.String(512), nullable=True),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_status", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("provenance_json", _json(), nullable=False),
        sa.Column("observation_key", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_demand_observation_key", "artist_demand_observation",
                    ["observation_key"], unique=True)
    op.create_index("ix_ado_artist_metric", "artist_demand_observation",
                    ["canonical_artist_id", "provider", "metric"])
    op.create_index("ix_ado_artist_scope", "artist_demand_observation",
                    ["canonical_artist_id", "scope_type", "scope_id"])
    op.create_index("ix_ado_observed_at", "artist_demand_observation", ["observed_at"])

    op.create_table(
        "provider_quota_day",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("quota_date", sa.Date(), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("search_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("non_search_quota_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("search_quota_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quota_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_provider_quota_day", "provider_quota_day",
                    ["provider", "quota_date"], unique=True)

    op.create_table(
        "demand_refresh_job",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("dedup_key", sa.String(700), nullable=False),
        sa.Column("canonical_artist_id", sa.String(512), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("job_type", sa.String(40), nullable=False),
        sa.Column("external_identity_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="PENDING"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(40), nullable=True),
        sa.Column("result_code", sa.String(40), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("lock_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", _json(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_demand_refresh_window", "demand_refresh_job", ["dedup_key"], unique=True)
    op.create_index("ix_demand_job_claimable", "demand_refresh_job", ["status", "scheduled_at"])
    op.create_index("ix_demand_job_artist", "demand_refresh_job", ["canonical_artist_id"])


def downgrade() -> None:
    op.drop_table("demand_refresh_job")
    op.drop_table("provider_quota_day")
    op.drop_table("artist_demand_observation")
    op.drop_table("artist_external_identity")

"""social evidence foundation (Phase 5C.1)

Revision ID: 008
Revises: 007

Adds the governed social spine: social_identity (canonical ↔ public social account associations with
watchlist scheduling state) and social_mention (durable, provenance-bearing social evidence — claims +
hash + provenance only, NO raw caption/media). Additive; existing supply/demand tables untouched.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


def _json():
    return JSONB().with_variant(sa.JSON(), "sqlite")


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "social_identity",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("canonical_entity_id", sa.String(600), nullable=False),
        sa.Column("canonical_entity_type", sa.String(24), nullable=False),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("platform_account_id", sa.String(255), nullable=True),
        sa.Column("handle", sa.String(255), nullable=False),
        sa.Column("account_url", sa.String(600), nullable=True),
        sa.Column("evidence_role", sa.String(40), nullable=False, server_default="OFFICIAL_ACCOUNT_EVIDENCE"),
        sa.Column("verification_state", sa.String(24), nullable=False, server_default="ASSERTED"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(64), nullable=False, server_default="OPERATOR"),
        sa.Column("collection_state", sa.String(24), nullable=False, server_default="ELIGIBLE"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_collected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_eligible_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_access_state", sa.String(32), nullable=True),
        sa.Column("provenance", _json(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_social_identity", "social_identity",
                    ["canonical_entity_id", "platform", "handle"], unique=True)
    op.create_index("ix_social_identity_canonical", "social_identity", ["canonical_entity_id"])
    op.create_index("ix_social_identity_due", "social_identity",
                    ["platform", "collection_state", "next_eligible_at"])

    op.create_table(
        "social_mention",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("source_account", sa.String(255), nullable=False),
        sa.Column("social_identity_id", sa.String(64), nullable=True),
        sa.Column("platform_post_id", sa.String(255), nullable=False),
        sa.Column("post_url", sa.String(600), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("canonical_entity_id", sa.String(600), nullable=True),
        sa.Column("canonical_entity_type", sa.String(24), nullable=True),
        sa.Column("linked_canonical_entity_ids", _json(), nullable=False, server_default="[]"),
        sa.Column("extracted_claims", _json(), nullable=False, server_default="{}"),
        sa.Column("evidence_role", sa.String(40), nullable=False, server_default="SOCIAL_DISCOVERY"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("parser_version", sa.String(48), nullable=False),
        sa.Column("content_hash", sa.String(80), nullable=True),
        sa.Column("provenance", _json(), nullable=False, server_default="{}"),
        sa.Column("processing_status", sa.String(24), nullable=False, server_default="UNPROCESSED"),
        sa.Column("claim_type", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_social_mention", "social_mention",
                    ["platform", "platform_post_id"], unique=True)
    op.create_index("ix_social_mention_canonical", "social_mention", ["canonical_entity_id"])
    op.create_index("ix_social_mention_processing", "social_mention", ["processing_status"])


def downgrade() -> None:
    op.drop_index("ix_social_mention_processing", table_name="social_mention")
    op.drop_index("ix_social_mention_canonical", table_name="social_mention")
    op.drop_index("uq_social_mention", table_name="social_mention")
    op.drop_table("social_mention")
    op.drop_index("ix_social_identity_due", table_name="social_identity")
    op.drop_index("ix_social_identity_canonical", table_name="social_identity")
    op.drop_index("uq_social_identity", table_name="social_identity")
    op.drop_table("social_identity")

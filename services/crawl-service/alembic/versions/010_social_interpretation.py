"""versioned social interpretation layer (Phase 5C.2)

Revision ID: 010
Revises: 009

Adds ``social_interpretation`` — a DERIVED, versioned deterministic interpretation of one specific
immutable ``social_mention`` version. This is the layer that turns observed evidence into a multi-label
classification + an event-bearing decision WITHOUT mutating the evidence, and records whether the
interpretation was projected into the EXISTING reconciliation machinery (``event_match_candidate``).
It never creates or mutates a canonical Event.

Mirrors the repo's temporal convention (``version`` + ``is_current`` + ``previous_interpretation_id``,
prior rows preserved). A changed classifier version inserts a new interpretation version; a partial
unique index enforces exactly one CURRENT interpretation per interpreted evidence version. Purely
additive + reversible (a brand-new table; social collection/interpretation is OFF in production).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def _json() -> JSON:
    return JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "social_interpretation",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("social_mention_id", sa.String(64), nullable=False),
        sa.Column("evidence_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("platform", sa.String(24), nullable=False),
        sa.Column("platform_post_id", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(80), nullable=True),
        sa.Column("canonical_entity_id", sa.String(600), nullable=True),
        sa.Column("classifier_version", sa.String(48), nullable=False),
        sa.Column("claim_types", _json(), nullable=False, server_default="[]"),
        sa.Column("primary_claim_type", sa.String(32), nullable=True),
        sa.Column("interpreted_fields", _json(), nullable=False, server_default="{}"),
        sa.Column("supporting_evidence", _json(), nullable=False, server_default="[]"),
        sa.Column("contradicting_evidence", _json(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reason_codes", _json(), nullable=False, server_default="[]"),
        sa.Column("event_bearing", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("event_candidate_status", sa.String(32), nullable=False, server_default="NONE"),
        sa.Column("matched_canonical_event_id", sa.String(512), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("event_match_candidate_id", sa.String(64), nullable=True),
        sa.Column("interpretation_status", sa.String(24), nullable=False, server_default="INTERPRETED"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("previous_interpretation_id", sa.String(64), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # Exactly one CURRENT interpretation per interpreted evidence version (idempotent target + invariant).
    op.create_index("uq_social_interpretation_current", "social_interpretation",
                    ["social_mention_id"], unique=True,
                    sqlite_where=sa.text("is_current"), postgresql_where=sa.text("is_current"))
    op.create_index("ix_social_interpretation_lineage", "social_interpretation",
                    ["social_mention_id", "version"])
    op.create_index("ix_social_interpretation_event_bearing", "social_interpretation",
                    ["event_bearing"])
    op.create_index("ix_social_interpretation_candidate_status", "social_interpretation",
                    ["event_candidate_status"])


def downgrade() -> None:
    op.drop_index("ix_social_interpretation_candidate_status", table_name="social_interpretation")
    op.drop_index("ix_social_interpretation_event_bearing", table_name="social_interpretation")
    op.drop_index("ix_social_interpretation_lineage", table_name="social_interpretation")
    op.drop_index("uq_social_interpretation_current", table_name="social_interpretation")
    op.drop_table("social_interpretation")

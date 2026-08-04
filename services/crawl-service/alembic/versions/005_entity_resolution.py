"""Phase 3.1 — cross-inventory entity resolution. Additive, reversible.

Adds entity_resolution_candidate, entity_source_handle, entity_resolution_history. Existing
tables/contracts unaffected; downgrade drops only the new tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_resolution_candidate",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=512), nullable=False),
        sa.Column("canonical_event_id", sa.String(length=512), nullable=True),
        sa.Column("source_entity_handle", sa.String(length=600), nullable=False),
        sa.Column("raw_name", sa.String(length=600), nullable=False),
        sa.Column("normalized_name", sa.String(length=600), nullable=False),
        sa.Column("candidate_canonical_entity_id", sa.String(length=600), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column("resolution_status", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=48), nullable=True),
        sa.Column("supporting_signals", sa.JSON(), nullable=False),
        sa.Column("contradicting_signals", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("resolver_version", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_entity_res_candidate", "entity_resolution_candidate",
                    ["entity_type", "source", "source_record_id", "source_entity_handle"], unique=True)
    op.create_index("ix_entity_res_status", "entity_resolution_candidate",
                    ["entity_type", "resolution_status"], unique=False)
    op.create_index("ix_entity_res_canonical", "entity_resolution_candidate",
                    ["candidate_canonical_entity_id"], unique=False)
    op.create_index("ix_entity_res_name", "entity_resolution_candidate",
                    ["entity_type", "normalized_name"], unique=False)

    op.create_table(
        "entity_source_handle",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_entity_handle", sa.String(length=600), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("canonical_entity_id", sa.String(length=600), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("resolution_method", sa.String(length=48), nullable=True),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_entity_source_handle", "entity_source_handle",
                    ["source", "source_entity_handle"], unique=True)
    op.create_index("ix_entity_handle_canonical", "entity_source_handle",
                    ["canonical_entity_id"], unique=False)

    op.create_table(
        "entity_resolution_history",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("previous_status", sa.String(length=24), nullable=True),
        sa.Column("new_status", sa.String(length=24), nullable=False),
        sa.Column("previous_canonical_entity_id", sa.String(length=600), nullable=True),
        sa.Column("new_canonical_entity_id", sa.String(length=600), nullable=True),
        sa.Column("reason_code", sa.String(length=48), nullable=True),
        sa.Column("resolver_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entity_res_history", "entity_resolution_history",
                    ["candidate_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_entity_res_history", table_name="entity_resolution_history")
    op.drop_table("entity_resolution_history")
    op.drop_index("ix_entity_handle_canonical", table_name="entity_source_handle")
    op.drop_index("uq_entity_source_handle", table_name="entity_source_handle")
    op.drop_table("entity_source_handle")
    op.drop_index("ix_entity_res_name", table_name="entity_resolution_candidate")
    op.drop_index("ix_entity_res_canonical", table_name="entity_resolution_candidate")
    op.drop_index("ix_entity_res_status", table_name="entity_resolution_candidate")
    op.drop_index("uq_entity_res_candidate", table_name="entity_resolution_candidate")
    op.drop_table("entity_resolution_candidate")

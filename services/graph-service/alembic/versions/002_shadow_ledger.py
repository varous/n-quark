"""Shadow Ledger tables (Phase 1) — additive, append-only.

Adds shadow_state + shadow_transition. Does not touch graph_nodes / graph_edges, so the existing
graph store, /v1/graph, and /v1/events contracts are unaffected. Fully reversible (downgrade drops
only the new tables).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_state",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("canonical_event_id", sa.String(length=512), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("source_record_id", sa.String(length=512), nullable=True),
        sa.Column("observation_id", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("normalized_state", sa.JSON(), nullable=False),
        sa.Column("epistemic_status", sa.String(length=64), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("previous_state_id", sa.String(length=64), nullable=True),
        sa.Column("detector_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shadow_state_event", "shadow_state", ["canonical_event_id"], unique=False)
    op.create_index(
        "ix_shadow_state_lookup",
        "shadow_state",
        ["canonical_event_id", "source_id", "source_record_id"],
        unique=False,
    )

    op.create_table(
        "shadow_transition",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("canonical_event_id", sa.String(length=512), nullable=False),
        sa.Column("source_id", sa.String(length=128), nullable=False),
        sa.Column("from_state_id", sa.String(length=64), nullable=True),
        sa.Column("to_state_id", sa.String(length=64), nullable=False),
        sa.Column("transition_type", sa.String(length=64), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=True),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("current_value", sa.JSON(), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("epistemic_status", sa.String(length=64), nullable=False),
        sa.Column("detector_version", sa.String(length=32), nullable=False),
        sa.Column("dedup_key", sa.String(length=700), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_shadow_transition_event", "shadow_transition", ["canonical_event_id"], unique=False
    )
    op.create_index(
        "uq_shadow_transition_dedup", "shadow_transition", ["dedup_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_shadow_transition_dedup", table_name="shadow_transition")
    op.drop_index("ix_shadow_transition_event", table_name="shadow_transition")
    op.drop_table("shadow_transition")
    op.drop_index("ix_shadow_state_lookup", table_name="shadow_state")
    op.drop_index("ix_shadow_state_event", table_name="shadow_state")
    op.drop_table("shadow_state")

"""Phase 3 — cross-platform event reconciliation. Additive, reversible.

Adds event_match_candidate. Existing tables/contracts unaffected; downgrade drops only the new table.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_match_candidate",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("pair_key", sa.String(length=1050), nullable=False),
        sa.Column("left_source", sa.String(length=64), nullable=False),
        sa.Column("left_source_record_id", sa.String(length=512), nullable=False),
        sa.Column("left_canonical_event_id", sa.String(length=512), nullable=True),
        sa.Column("right_source", sa.String(length=64), nullable=False),
        sa.Column("right_source_record_id", sa.String(length=512), nullable=False),
        sa.Column("right_canonical_event_id", sa.String(length=512), nullable=True),
        sa.Column("match_status", sa.String(length=24), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=False),
        sa.Column("component_scores", sa.JSON(), nullable=False),
        sa.Column("supporting_signals", sa.JSON(), nullable=False),
        sa.Column("contradicting_signals", sa.JSON(), nullable=False),
        sa.Column("reason_code", sa.String(length=48), nullable=True),
        sa.Column("matcher_version", sa.String(length=32), nullable=False),
        sa.Column("review_status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_event_match_pair", "event_match_candidate", ["pair_key"], unique=True)
    op.create_index("ix_event_match_status", "event_match_candidate", ["match_status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_event_match_status", table_name="event_match_candidate")
    op.drop_index("uq_event_match_pair", table_name="event_match_candidate")
    op.drop_table("event_match_candidate")

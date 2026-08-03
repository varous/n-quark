"""Phase 2.2 — live enrichment pilot. Additive, reversible.

Adds source-family provenance columns to enrichment_candidate + an enrichment_run audit table.
Existing rows/contracts are unaffected; downgrade drops only the new objects.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CAND_COLS = (
    ("surface", sa.String(length=40)),
    ("source_family", sa.String(length=40)),
    ("independence_group", sa.String(length=40)),
)


def upgrade() -> None:
    for name, coltype in _CAND_COLS:
        op.add_column("enrichment_candidate", sa.Column(name, coltype, nullable=True))

    op.create_table(
        "enrichment_run",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("surface", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("events_selected", sa.Integer(), nullable=False),
        sa.Column("pages_attempted", sa.Integer(), nullable=False),
        sa.Column("pages_retrieved", sa.Integer(), nullable=False),
        sa.Column("candidates_created", sa.Integer(), nullable=False),
        sa.Column("resolutions_changed", sa.Integer(), nullable=False),
        sa.Column("conflicts_found", sa.Integer(), nullable=False),
        sa.Column("parser_failures", sa.Integer(), nullable=False),
        sa.Column("request_latency_ms", sa.Integer(), nullable=False),
        sa.Column("bytes_received", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("config_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_enrichment_run_started", "enrichment_run", ["started_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_enrichment_run_started", table_name="enrichment_run")
    op.drop_table("enrichment_run")
    for name, _ in reversed(_CAND_COLS):
        op.drop_column("enrichment_candidate", name)

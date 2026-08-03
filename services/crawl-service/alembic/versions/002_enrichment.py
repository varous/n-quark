"""Phase 2.1 — evidence-based capture enrichment. Additive, reversible.

Adds enrichment_candidate + event_field_resolution, plus enrichment-derived columns on
tracked_event. Existing rows/contracts are unaffected; downgrade drops only the new objects.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRACKED_COLS = (
    ("region_id", sa.String(length=512)),
    ("event_status", sa.String(length=40)),
    ("source_on_sale_at", sa.DateTime(timezone=True)),
    ("first_ticket_state_seen_at", sa.DateTime(timezone=True)),
    ("last_enriched_at", sa.DateTime(timezone=True)),
    ("enrichment_status", sa.String(length=24)),
    ("enrichment_confidence", sa.Float()),
)


def upgrade() -> None:
    for name, coltype in _TRACKED_COLS:
        op.add_column("tracked_event", sa.Column(name, coltype, nullable=True))

    op.create_table(
        "enrichment_candidate",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("canonical_event_id", sa.String(length=512), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=512), nullable=True),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("candidate_value", sa.JSON(), nullable=True),
        sa.Column("normalized_value", sa.JSON(), nullable=True),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("extraction_method", sa.String(length=40), nullable=False),
        sa.Column("epistemic_status", sa.String(length=40), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("candidate_status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_enrichment_candidate_event_field", "enrichment_candidate",
                    ["canonical_event_id", "field_name"], unique=False)
    op.create_index("uq_enrichment_candidate_hash", "enrichment_candidate",
                    ["canonical_event_id", "field_name", "content_hash"], unique=True)

    op.create_table(
        "event_field_resolution",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("canonical_event_id", sa.String(length=512), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("resolved_value", sa.JSON(), nullable=True),
        sa.Column("resolution_method", sa.String(length=40), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("review_status", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=48), nullable=True),
        sa.Column("supporting_candidate_ids", sa.JSON(), nullable=False),
        sa.Column("contradicting_candidate_ids", sa.JSON(), nullable=False),
        sa.Column("resolver_version", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_field_resolution", "event_field_resolution",
                    ["canonical_event_id", "field_name", "is_current"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_event_field_resolution", table_name="event_field_resolution")
    op.drop_table("event_field_resolution")
    op.drop_index("uq_enrichment_candidate_hash", table_name="enrichment_candidate")
    op.drop_index("ix_enrichment_candidate_event_field", table_name="enrichment_candidate")
    op.drop_table("enrichment_candidate")
    for name, _ in reversed(_TRACKED_COLS):
        op.drop_column("tracked_event", name)

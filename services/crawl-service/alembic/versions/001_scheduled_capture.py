"""Scheduled capture (Phase 2) — tracked_event + scheduled_capture_job. Additive, reversible."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracked_event",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=512), nullable=False),
        sa.Column("canonical_event_id", sa.String(length=512), nullable=True),
        sa.Column("city", sa.String(length=255), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("on_sale_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tracking_status", sa.String(length=24), nullable=False),
        sa.Column("first_tracked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_record_present_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_state_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_capture_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cadence_reason", sa.String(length=64), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("priority_reason", sa.JSON(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("consecutive_absences", sa.Integer(), nullable=False),
        sa.Column("capture_count", sa.Integer(), nullable=False),
        sa.Column("distinct_state_count", sa.Integer(), nullable=False),
        sa.Column("transition_count", sa.Integer(), nullable=False),
        sa.Column("last_capture_status", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_tracked_event", "tracked_event", ["source", "source_record_id"], unique=True)
    op.create_index("ix_tracked_event_due", "tracked_event", ["next_capture_at"], unique=False)

    op.create_table(
        "scheduled_capture_job",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("dedup_key", sa.String(length=700), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_record_id", sa.String(length=512), nullable=False),
        sa.Column("canonical_event_id", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_capture_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=40), nullable=True),
        sa.Column("result_code", sa.String(length=40), nullable=True),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("lock_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_capture_job_window", "scheduled_capture_job", ["dedup_key"], unique=True)
    op.create_index(
        "ix_capture_job_claimable", "scheduled_capture_job", ["status", "scheduled_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_capture_job_claimable", table_name="scheduled_capture_job")
    op.drop_index("uq_capture_job_window", table_name="scheduled_capture_job")
    op.drop_table("scheduled_capture_job")
    op.drop_index("ix_tracked_event_due", table_name="tracked_event")
    op.drop_index("uq_tracked_event", table_name="tracked_event")
    op.drop_table("tracked_event")

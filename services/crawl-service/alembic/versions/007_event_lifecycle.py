"""event lifecycle scheduling evidence

Revision ID: 007
Revises: 006
"""
from alembic import op
import sqlalchemy as sa

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracked_event", sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("tracked_event", sa.Column("event_date", sa.String(10), nullable=True))
    op.add_column("tracked_event", sa.Column("source_time_precision", sa.String(32), nullable=True))
    op.add_column("tracked_event", sa.Column("source_timezone", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("tracked_event", "source_timezone")
    op.drop_column("tracked_event", "source_time_precision")
    op.drop_column("tracked_event", "event_date")
    op.drop_column("tracked_event", "ends_at")

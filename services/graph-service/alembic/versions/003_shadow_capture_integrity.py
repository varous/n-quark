"""Phase 1.1 — capture completeness + transition integrity (additive, reversible).

Adds capture-integrity columns to shadow_state. All nullable / defaulted so existing rows stay
valid and existing callers keep working. No data is rewritten; downgrade drops only the new columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("shadow_state", sa.Column("snapshot_completeness", sa.String(length=16), nullable=True))
    op.add_column("shadow_state", sa.Column("capture_status", sa.String(length=40), nullable=True))
    op.add_column("shadow_state", sa.Column("field_status", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("shadow_state", sa.Column("capture_hash", sa.String(length=64), nullable=True))
    op.add_column("shadow_state", sa.Column("out_of_order", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("shadow_state", sa.Column("absence_count", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("shadow_state", "absence_count")
    op.drop_column("shadow_state", "out_of_order")
    op.drop_column("shadow_state", "capture_hash")
    op.drop_column("shadow_state", "field_status")
    op.drop_column("shadow_state", "capture_status")
    op.drop_column("shadow_state", "snapshot_completeness")

"""Initial observations table (append-only)."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "observations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity", sa.String(length=512), nullable=False),
        sa.Column("attribute", sa.String(length=255), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_observations_entity", "observations", ["entity"], unique=False)
    op.create_index(
        "ix_observations_entity_timestamp",
        "observations",
        ["entity", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_observations_source_timestamp",
        "observations",
        ["source", "timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_observations_source_timestamp", table_name="observations")
    op.drop_index("ix_observations_entity_timestamp", table_name="observations")
    op.drop_index("ix_observations_entity", table_name="observations")
    op.drop_table("observations")

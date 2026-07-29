"""Canonical entities and alias mapping tables."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.String(length=512), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entities_entity_type", "entities", ["entity_type"], unique=False)

    op.create_table(
        "entity_aliases",
        sa.Column("alias_key", sa.String(length=512), nullable=False),
        sa.Column("canonical_id", sa.String(length=512), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["canonical_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("alias_key"),
    )
    op.create_index("ix_entity_aliases_canonical_id", "entity_aliases", ["canonical_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_entity_aliases_canonical_id", table_name="entity_aliases")
    op.drop_table("entity_aliases")
    op.drop_index("ix_entities_entity_type", table_name="entities")
    op.drop_table("entities")

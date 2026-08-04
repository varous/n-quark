"""Admin Phase B — non-destructive legacy/canonical supersession. Additive, reversible.

Adds entity_supersession. Existing tables/contracts unaffected; downgrade drops only the new table.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_supersession",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("legacy_entity_id", sa.String(length=600), nullable=False),
        sa.Column("canonical_entity_id", sa.String(length=600), nullable=False),
        sa.Column("relationship", sa.String(length=24), nullable=False),
        sa.Column("decision_ref", sa.String(length=120), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_entity_supersession", "entity_supersession", ["legacy_entity_id"], unique=True)
    op.create_index("ix_entity_supersession_canonical", "entity_supersession", ["canonical_entity_id"])


def downgrade() -> None:
    op.drop_index("ix_entity_supersession_canonical", table_name="entity_supersession")
    op.drop_index("uq_entity_supersession", table_name="entity_supersession")
    op.drop_table("entity_supersession")

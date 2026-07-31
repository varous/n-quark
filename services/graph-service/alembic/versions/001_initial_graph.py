"""Initial graph tables (nodes + edges) for the Postgres-backed graph store."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.String(length=512), nullable=False),
        sa.Column("type", sa.String(length=128), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_graph_nodes_type", "graph_nodes", ["type"], unique=False)

    op.create_table(
        "graph_edges",
        sa.Column("source", sa.String(length=512), nullable=False),
        sa.Column("relationship", sa.String(length=128), nullable=False),
        sa.Column("target", sa.String(length=512), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("source", "relationship", "target", name="pk_graph_edges"),
    )
    op.create_index("ix_graph_edges_source", "graph_edges", ["source"], unique=False)
    op.create_index("ix_graph_edges_target", "graph_edges", ["target"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_graph_edges_target", table_name="graph_edges")
    op.drop_index("ix_graph_edges_source", table_name="graph_edges")
    op.drop_table("graph_edges")
    op.drop_index("ix_graph_nodes_type", table_name="graph_nodes")
    op.drop_table("graph_nodes")

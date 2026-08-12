"""Phase 5B.1 — artist intake & research watchlists.

Additive and reversible. Adds a single table, ``artist_watch_target`` — the operator's durable
instruction to attempt and maintain observation of an artist. It is NOT canonical identity: canonical
artists remain owned by the entity/graph architecture, and this table only records intent + the
resolution outcome. Touches no existing table.

Revision ID: 003_watch_targets
Revises: 002_artist_universe
Create Date: 2026-08-12
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "003_watch_targets"
down_revision = "002_artist_universe"
branch_labels = None
depends_on = None


def _json():
    return sa.JSON().with_variant(JSONB, "postgresql")


def upgrade() -> None:
    op.create_table(
        "artist_watch_target",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(600), nullable=False),
        sa.Column("dedup_key", sa.String(700), nullable=False),
        sa.Column("canonical_artist_id", sa.String(512), nullable=True),
        sa.Column("youtube_hint", sa.String(1024), nullable=True),
        sa.Column("youtube_channel_id", sa.String(64), nullable=True),
        sa.Column("source", sa.String(48), nullable=False, server_default="OPERATOR"),
        sa.Column("reason", sa.String(600), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("status", sa.String(24), nullable=False, server_default="NEW"),
        sa.Column("resolution_method", sa.String(64), nullable=True),
        sa.Column("detail", _json(), nullable=False),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("last_resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("uq_artist_watch_target_dedup", "artist_watch_target", ["dedup_key"], unique=True)
    op.create_index("ix_awt_status", "artist_watch_target", ["status"])
    op.create_index("ix_awt_canonical", "artist_watch_target", ["canonical_artist_id"])


def downgrade() -> None:
    op.drop_index("ix_awt_canonical", table_name="artist_watch_target")
    op.drop_index("ix_awt_status", table_name="artist_watch_target")
    op.drop_index("uq_artist_watch_target_dedup", table_name="artist_watch_target")
    op.drop_table("artist_watch_target")

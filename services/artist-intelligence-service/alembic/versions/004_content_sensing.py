"""Phase 5B.2 — YouTube content sensing: owned vs ecosystem content + availability.

Additive and reversible. Extends the existing ``youtube_video`` registry with a relationship type
(OWNED_CONTENT vs ECOSYSTEM_CONTENT), an explicit provider availability state (AVAILABLE / UNAVAILABLE /
NOT_FOUND), and the discovery method. Existing rows are OWNED_CONTENT / AVAILABLE by back-fill default
(they were discovered from the artist's own verified channel). No statistics/observation table changes —
per-video time-series already lives in ``artist_demand_observation`` (scope_type=CONTENT).

Revision ID: 004_content_sensing
Revises: 003_watch_targets
Create Date: 2026-08-12
"""

import sqlalchemy as sa

from alembic import op

revision = "004_content_sensing"
down_revision = "003_watch_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("youtube_video", sa.Column(
        "relationship_type", sa.String(24), nullable=False, server_default="OWNED_CONTENT"))
    op.add_column("youtube_video", sa.Column(
        "availability_state", sa.String(16), nullable=False, server_default="AVAILABLE"))
    op.add_column("youtube_video", sa.Column("discovery_method", sa.String(48), nullable=True))
    op.create_index("ix_ytv_relationship", "youtube_video", ["canonical_artist_id", "relationship_type"])


def downgrade() -> None:
    op.drop_index("ix_ytv_relationship", table_name="youtube_video")
    op.drop_column("youtube_video", "discovery_method")
    op.drop_column("youtube_video", "availability_state")
    op.drop_column("youtube_video", "relationship_type")

"""immutable social evidence versions (Phase 5C.1.1)

Revision ID: 009
Revises: 008

Makes ``social_mention`` an append-only, versioned evidence table. A *logical* social post is
``(platform, platform_post_id)``; each materially different observed content state becomes a distinct
immutable version (``version`` + ``is_current`` + ``previous_mention_id``), mirroring
``event_field_resolution``. A changed source post therefore INSERTS a new version and can never erase a
previously observed factual state.

Schema change: the old unique index on ``(platform, platform_post_id)`` (which forced one mutable row per
post) is replaced by a **partial** unique index on the same tuple WHERE ``is_current`` — one current
version per logical post — plus a non-unique ``(platform, platform_post_id, version)`` lineage index.
Additive + reversible; existing rows (production social collection is OFF, tables effectively empty)
default to version 1 / is_current true, so they remain valid.
"""
from alembic import op
import sqlalchemy as sa


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("social_mention",
                  sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("social_mention",
                  sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.add_column("social_mention",
                  sa.Column("previous_mention_id", sa.String(64), nullable=True))
    op.add_column("social_mention",
                  sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))

    # Replace the one-row-per-post unique index with one-CURRENT-version-per-post + a lineage index.
    op.drop_index("uq_social_mention", table_name="social_mention")
    op.create_index("uq_social_mention_current", "social_mention",
                    ["platform", "platform_post_id"], unique=True,
                    sqlite_where=sa.text("is_current"), postgresql_where=sa.text("is_current"))
    op.create_index("ix_social_mention_post", "social_mention",
                    ["platform", "platform_post_id", "version"])


def downgrade() -> None:
    op.drop_index("ix_social_mention_post", table_name="social_mention")
    op.drop_index("uq_social_mention_current", table_name="social_mention")
    op.create_index("uq_social_mention", "social_mention",
                    ["platform", "platform_post_id"], unique=True)
    op.drop_column("social_mention", "superseded_at")
    op.drop_column("social_mention", "previous_mention_id")
    op.drop_column("social_mention", "is_current")
    op.drop_column("social_mention", "version")

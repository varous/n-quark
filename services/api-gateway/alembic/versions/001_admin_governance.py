"""Admin Phase B — gateway governance schema. Additive, reversible.

Creates admin_audit_log + admin_resolution_decision. First gateway migration (replaces the Phase A
runtime create_all for the audit table). Downgrade drops only these tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has(table: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(table)


def upgrade() -> None:
    # admin_audit_log may already exist from the Phase A runtime create_all — adopt it idempotently
    # rather than fail; this migration is the first-ever on a fresh DB, where neither table exists.
    if _has("admin_audit_log"):
        for ix in ("ix_admin_audit_created", "ix_admin_audit_object", "ix_admin_audit_request"):
            try:
                op.create_index(ix, "admin_audit_log",
                                {"ix_admin_audit_created": ["created_at"],
                                 "ix_admin_audit_object": ["object_type", "object_id"],
                                 "ix_admin_audit_request": ["request_id"]}[ix])
            except Exception:  # noqa: BLE001 — index may already exist
                pass
        _create_decision()
        return
    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("actor_role", sa.String(length=24), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.String(length=600), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("previous_value", sa.JSON(), nullable=True),
        sa.Column("new_value", sa.JSON(), nullable=True),
        sa.Column("reason", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_admin_audit_created", "admin_audit_log", ["created_at"])
    op.create_index("ix_admin_audit_object", "admin_audit_log", ["object_type", "object_id"])
    op.create_index("ix_admin_audit_request", "admin_audit_log", ["request_id"])
    _create_decision()


def _create_decision() -> None:
    if _has("admin_resolution_decision"):
        return
    op.create_table(
        "admin_resolution_decision",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("candidate_id", sa.String(length=64), nullable=True),
        sa.Column("entity_type", sa.String(length=24), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("source_record_id", sa.String(length=512), nullable=True),
        sa.Column("source_handle", sa.String(length=600), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("previous_status", sa.String(length=24), nullable=True),
        sa.Column("new_status", sa.String(length=24), nullable=True),
        sa.Column("previous_canonical_entity_id", sa.String(length=600), nullable=True),
        sa.Column("selected_canonical_entity_id", sa.String(length=600), nullable=True),
        sa.Column("created_canonical_entity_id", sa.String(length=600), nullable=True),
        sa.Column("supersedes_decision_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=False),
        sa.Column("actor_role", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=48), nullable=True),
        sa.Column("note", sa.String(length=2048), nullable=True),
        sa.Column("impact_snapshot", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_by", sa.String(length=255), nullable=True),
        sa.Column("reversed_by_decision_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("uq_admin_decision_idem", "admin_resolution_decision", ["idempotency_key"], unique=True)
    op.create_index("ix_admin_decision_candidate", "admin_resolution_decision", ["candidate_id"])
    op.create_index("ix_admin_decision_created", "admin_resolution_decision", ["created_at"])
    op.create_index("ix_admin_decision_entity", "admin_resolution_decision", ["selected_canonical_entity_id"])


def downgrade() -> None:
    op.drop_index("ix_admin_decision_entity", table_name="admin_resolution_decision")
    op.drop_index("ix_admin_decision_created", table_name="admin_resolution_decision")
    op.drop_index("ix_admin_decision_candidate", table_name="admin_resolution_decision")
    op.drop_index("uq_admin_decision_idem", table_name="admin_resolution_decision")
    op.drop_table("admin_resolution_decision")
    op.drop_index("ix_admin_audit_request", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_object", table_name="admin_audit_log")
    op.drop_index("ix_admin_audit_created", table_name="admin_audit_log")
    op.drop_table("admin_audit_log")

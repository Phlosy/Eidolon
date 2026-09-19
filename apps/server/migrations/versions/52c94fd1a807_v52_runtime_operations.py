"""v52: immutable runtime request identities and per-action audit receipts."""

import sqlalchemy as sa
from alembic import op

revision = "52c94fd1a807"
down_revision = "51b83e04a6f2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runtime_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column(
            "work_session_id", sa.Integer(), sa.ForeignKey("work_sessions.id"), nullable=False
        ),
        sa.Column("operation_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("requested_action_count", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.Integer(), sa.ForeignKey("decision_records.id"), nullable=True),
        sa.CheckConstraint(
            "(kind = 'read' AND requested_action_count = 1) OR "
            "(kind = 'decision' AND requested_action_count BETWEEN 1 AND 16) OR "
            "(kind = 'finish' AND requested_action_count = 0)",
            name="ck_runtime_operation_kind_count",
        ),
        sa.CheckConstraint(
            "decision_id IS NULL OR kind = 'decision'", name="ck_runtime_operation_decision"
        ),
        sa.CheckConstraint(
            "length(operation_key) BETWEEN 1 AND 128 AND length(request_digest) = 64",
            name="ck_runtime_operation_identity_length",
        ),
    )
    op.create_index(
        "uq_runtime_operation_key",
        "runtime_operations",
        ["work_session_id", "operation_key"],
        unique=True,
    )
    op.add_column("tool_audits", sa.Column("runtime_operation_id", sa.Integer(), nullable=True))
    op.add_column("tool_audits", sa.Column("action_index", sa.Integer(), nullable=True))
    op.create_index(
        "uq_tool_audit_runtime_action",
        "tool_audits",
        ["runtime_operation_id", "action_index"],
        unique=True,
    )
    # Keep these guards revision-local: a future model change must not rewrite history.
    identity_changed = " OR ".join(
        f"NEW.{name} IS NOT OLD.{name}"
        for name in (
            "id",
            "created_at",
            "work_session_id",
            "operation_key",
            "request_digest",
            "kind",
            "requested_action_count",
        )
    )
    invalid_receipt = (
        "(NEW.runtime_operation_id IS NULL) <> (NEW.action_index IS NULL) OR "
        "(NEW.runtime_operation_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM runtime_operations op WHERE op.id = NEW.runtime_operation_id "
        "AND NEW.action_index >= 0 AND NEW.action_index < op.requested_action_count "
        "AND NEW.work_session_id = op.work_session_id "
        "AND NEW.decision_id IS op.decision_id "
        "AND (op.kind = 'read' OR (op.kind = 'decision' AND op.decision_id IS NOT NULL))))"
    )
    for table, name, timing, condition, message in (
        (
            "runtime_operations",
            "runtime_operation_identity_immutable",
            "UPDATE",
            identity_changed,
            "runtime operation identity is immutable",
        ),
        (
            "runtime_operations",
            "runtime_operation_decision_once",
            "UPDATE",
            "OLD.decision_id IS NOT NULL AND NEW.decision_id IS NOT OLD.decision_id",
            "runtime operation decision is already bound",
        ),
        (
            "runtime_operations",
            "runtime_operation_no_delete",
            "DELETE",
            "1",
            "runtime operation identity is immutable",
        ),
        (
            "tool_audits",
            "runtime_receipt_insert_valid",
            "INSERT",
            invalid_receipt,
            "invalid runtime receipt correlation",
        ),
        (
            "tool_audits",
            "runtime_receipt_update_valid",
            "UPDATE",
            invalid_receipt,
            "invalid runtime receipt correlation",
        ),
        (
            "tool_audits",
            "runtime_receipt_immutable",
            "UPDATE",
            "OLD.runtime_operation_id IS NOT NULL",
            "runtime receipt is immutable",
        ),
        (
            "tool_audits",
            "runtime_receipt_no_delete",
            "DELETE",
            "OLD.runtime_operation_id IS NOT NULL",
            "runtime receipt is immutable",
        ),
    ):
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {timing} ON {table} WHEN {condition} "
            f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )


def downgrade():
    for name in (
        "runtime_receipt_no_delete",
        "runtime_receipt_immutable",
        "runtime_receipt_update_valid",
        "runtime_receipt_insert_valid",
        "runtime_operation_no_delete",
        "runtime_operation_decision_once",
        "runtime_operation_identity_immutable",
    ):
        op.execute(f"DROP TRIGGER {name}")
    op.drop_index("uq_tool_audit_runtime_action", table_name="tool_audits")
    op.drop_column("tool_audits", "action_index")
    op.drop_column("tool_audits", "runtime_operation_id")
    op.drop_index("uq_runtime_operation_key", table_name="runtime_operations")
    op.drop_table("runtime_operations")

"""v64: accepted structured input and once-only original reply correlation."""

import sqlalchemy as sa
from alembic import op

revision = "64da10b82e39"
down_revision = "63c98cff1a27"
branch_labels = None
depends_on = None

_BODY_GUARDS = (
    (
        "runtime_operation_body_valid",
        "INSERT",
        "NEW.request_body IS NOT NULL AND CASE WHEN typeof(NEW.request_body) <> 'text' OR "
        "length(CAST(NEW.request_body AS BLOB)) > 65536 OR json_valid(NEW.request_body) <> 1 "
        "THEN 1 ELSE json_type(NEW.request_body) IS NOT 'object' OR "
        "json_extract(NEW.request_body, '$.version') IS NOT 1 OR "
        "json_extract(NEW.request_body, '$.kind') IS NOT NEW.kind OR "
        "json_extract(NEW.request_body, '$.operation_key') IS NOT NEW.operation_key OR "
        "(NEW.kind = 'decision' AND json_array_length(NEW.request_body, "
        "'$.decision.actions') IS NOT NEW.requested_action_count) END",
    ),
    ("runtime_operation_body_immutable", "UPDATE", "NEW.request_body IS NOT OLD.request_body"),
    (
        "runtime_operation_no_replace",
        "INSERT",
        "EXISTS (SELECT 1 FROM runtime_operations op WHERE op.id=NEW.id OR "
        "(op.work_session_id=NEW.work_session_id AND op.operation_key=NEW.operation_key))",
    ),
)

_REPLY_GUARDS = (
    ("runtime_attempt_reply_insert", "INSERT", "NEW.reply_operation_id IS NOT NULL"),
    (
        "runtime_attempt_reply_once",
        "UPDATE",
        "OLD.reply_operation_id IS NOT NULL AND NEW.reply_operation_id IS NOT "
        "OLD.reply_operation_id",
    ),
    (
        "runtime_attempt_reply_valid",
        "UPDATE",
        "NEW.reply_operation_id IS NOT OLD.reply_operation_id AND (NEW.kind <> 'controlled' "
        "OR NEW.status <> 'completed' OR NEW.host_outcome IS NOT 'completed' OR "
        "NEW.host_reason IS NOT 'reply_received' OR NEW.stop_requested_at IS NOT NULL OR NOT "
        "EXISTS (SELECT 1 FROM runtime_operations op WHERE op.id=NEW.reply_operation_id AND "
        "op.work_session_id=NEW.work_session_id) OR EXISTS (SELECT 1 FROM runtime_attempts a "
        "WHERE a.work_session_id=NEW.work_session_id AND a.id>NEW.id))",
    ),
)


def _sqlite_transaction():
    connection = op.get_bind()
    if (
        connection.dialect.name == "sqlite"
        and not connection.connection.driver_connection.in_transaction
    ):
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade():
    _sqlite_transaction()
    op.add_column("runtime_operations", sa.Column("request_body", sa.Text(), nullable=True))
    op.add_column("runtime_attempts", sa.Column("reply_operation_id", sa.Integer(), nullable=True))
    if op.get_bind().dialect.name == "sqlite":
        for table, guards, message in (
            ("runtime_operations", _BODY_GUARDS, "invalid runtime request source"),
            ("runtime_attempts", _REPLY_GUARDS, "invalid runtime execution fact"),
        ):
            for name, operation, condition in guards:
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {operation} ON {table} WHEN {condition} "
                    f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
                )


def downgrade():
    _sqlite_transaction()
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM runtime_operations "
                "WHERE request_body IS NOT NULL) OR "
                "EXISTS (SELECT 1 FROM runtime_attempts WHERE reply_operation_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("v64 downgrade would erase accepted runtime request sources")
    if op.get_bind().dialect.name == "sqlite":
        for name, _, _ in (*_REPLY_GUARDS, *_BODY_GUARDS):
            op.execute(f"DROP TRIGGER {name}")
    op.drop_column("runtime_attempts", "reply_operation_id")
    op.drop_column("runtime_operations", "request_body")

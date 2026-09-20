"""v62: first stop intent on the original execution; no invented historical stops."""

import sqlalchemy as sa
from alembic import op

revision = "62b87bef0916"
down_revision = "61a76adf9805"
branch_labels = None
depends_on = None

_STOP_VALID = (
    "(NEW.stop_requested_at IS NULL) <> (NEW.stop_reason IS NULL) OR "
    "(NEW.stop_reason IS NOT NULL AND (NEW.stop_reason NOT IN ("
    "'cancelled', 'budget_denied', 'reassigned', 'blocked', 'shutdown', 'host_error', "
    "'runtime_error', 'timeout') OR julianday(NEW.stop_requested_at) IS NULL))"
)
_GUARDS = (
    (
        "runtime_attempt_stop_insert",
        "INSERT",
        "NEW.stop_requested_at IS NOT NULL OR NEW.stop_reason IS NOT NULL",
    ),
    ("runtime_attempt_stop_valid", "UPDATE", _STOP_VALID),
    (
        "runtime_attempt_stop_once",
        "UPDATE",
        "OLD.stop_requested_at IS NOT NULL AND (NEW.stop_requested_at IS NOT "
        "OLD.stop_requested_at OR NEW.stop_reason IS NOT OLD.stop_reason)",
    ),
    (
        "runtime_attempt_no_replace",
        "INSERT",
        "EXISTS (SELECT 1 FROM runtime_attempts a WHERE a.id=NEW.id OR "
        "a.idempotency_key=NEW.idempotency_key)",
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
    op.add_column("runtime_attempts", sa.Column("stop_requested_at", sa.DateTime(), nullable=True))
    op.add_column("runtime_attempts", sa.Column("stop_reason", sa.String(48), nullable=True))
    if op.get_bind().dialect.name == "sqlite":
        for name, operation, condition in _GUARDS:
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON runtime_attempts WHEN {condition} "
                "BEGIN SELECT RAISE(ABORT, 'invalid runtime execution fact'); END"
            )
        for name, operation, ref in (
            ("work_session_execution_no_replace", "INSERT", "NEW"),
            ("work_session_execution_no_delete", "DELETE", "OLD"),
        ):
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON work_sessions WHEN "
                f"EXISTS (SELECT 1 FROM runtime_attempts a WHERE a.work_session_id={ref}.id) "
                "BEGIN SELECT RAISE(ABORT, 'invalid runtime execution fact'); END"
            )
        op.execute(
            "CREATE TRIGGER artifact_collection_stop_fence BEFORE INSERT "
            "ON runtime_artifact_collections "
            "WHEN EXISTS (SELECT 1 FROM runtime_attempts a WHERE a.id=NEW.attempt_id "
            "AND a.stop_requested_at IS NOT NULL) "
            "BEGIN SELECT RAISE(ABORT, 'invalid or immutable artifact collection'); END"
        )


def downgrade():
    _sqlite_transaction()
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM runtime_attempts WHERE stop_requested_at IS NOT NULL "
                "OR stop_reason IS NOT NULL"
            )
        )
        .scalar()
    ):
        raise RuntimeError("v62 downgrade would erase durable stop intent")
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER artifact_collection_stop_fence")
        op.execute("DROP TRIGGER work_session_execution_no_replace")
        op.execute("DROP TRIGGER work_session_execution_no_delete")
        for name, _, _ in _GUARDS:
            op.execute(f"DROP TRIGGER {name}")
    op.drop_column("runtime_attempts", "stop_reason")
    op.drop_column("runtime_attempts", "stop_requested_at")

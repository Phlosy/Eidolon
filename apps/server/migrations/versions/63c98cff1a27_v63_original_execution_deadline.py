"""v63: immutable original request deadlines; legacy limits stay unknown."""

import sqlalchemy as sa
from alembic import op

revision = "63c98cff1a27"
down_revision = "62b87bef0916"
branch_labels = None
depends_on = None

_GUARDS = (
    (
        "runtime_attempt_deadline_insert",
        "INSERT",
        "(NEW.deadline_at IS NOT NULL AND julianday(NEW.deadline_at) IS NULL) OR "
        "EXISTS (SELECT 1 FROM runtime_attempts a WHERE a.work_session_id=NEW.work_session_id "
        "AND a.request_key=NEW.request_key AND a.deadline_at IS NOT NEW.deadline_at)",
    ),
    (
        "runtime_attempt_deadline_immutable",
        "UPDATE",
        "NEW.deadline_at IS NOT OLD.deadline_at",
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
    op.add_column("runtime_attempts", sa.Column("deadline_at", sa.DateTime(), nullable=True))
    if op.get_bind().dialect.name == "sqlite":
        for name, operation, condition in _GUARDS:
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON runtime_attempts WHEN {condition} "
                "BEGIN SELECT RAISE(ABORT, 'invalid runtime execution fact'); END"
            )


def downgrade():
    _sqlite_transaction()
    if (
        op.get_bind()
        .execute(sa.text("SELECT COUNT(*) FROM runtime_attempts WHERE deadline_at IS NOT NULL"))
        .scalar()
    ):
        raise RuntimeError("v63 downgrade would erase original execution deadlines")
    if op.get_bind().dialect.name == "sqlite":
        for name, _, _ in _GUARDS:
            op.execute(f"DROP TRIGGER {name}")
    op.drop_column("runtime_attempts", "deadline_at")

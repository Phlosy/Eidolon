"""v58: immutable original workspace/requirements on new real worker attempts."""

import sqlalchemy as sa
from alembic import op

revision = "58d437ac6502"
down_revision = "57c326fb5401"
branch_labels = None
depends_on = None

_IDENTITY = (
    "id",
    "created_at",
    "work_session_id",
    "request_key",
    "attempt_number",
    "idempotency_key",
    "request_digest",
    "local_session_ref",
    "runtime_instance_id",
    "instance_fingerprint",
    "kind",
    "remote_session_ref",
)
_CONTEXT_VALID = (
    "NEW.artifact_context_json IS NOT NULL AND (NEW.kind <> 'worker' OR "
    "NEW.runtime_instance_id IS NULL OR json_valid(NEW.artifact_context_json) <> 1 OR "
    "json_type(NEW.artifact_context_json) IS NOT 'object')"
)


def _sqlite_transaction():
    connection = op.get_bind()
    if (
        connection.dialect.name == "sqlite"
        and not connection.connection.driver_connection.in_transaction
    ):
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def _identity_guard(columns):
    condition = " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in columns)
    op.execute(
        "CREATE TRIGGER runtime_attempt_identity_immutable BEFORE UPDATE ON runtime_attempts "
        f"WHEN {condition} BEGIN SELECT RAISE(ABORT, 'invalid runtime execution fact'); END"
    )


def upgrade():
    _sqlite_transaction()
    op.add_column(
        "runtime_attempts",
        sa.Column("artifact_context_json", sa.JSON(none_as_null=True), nullable=True),
    )
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER runtime_attempt_identity_immutable")
        _identity_guard((*_IDENTITY, "artifact_context_json"))
        op.execute(
            "CREATE TRIGGER runtime_attempt_artifact_context_valid BEFORE INSERT "
            f"ON runtime_attempts WHEN {_CONTEXT_VALID} "
            "BEGIN SELECT RAISE(ABORT, 'invalid runtime execution fact'); END"
        )


def downgrade():
    _sqlite_transaction()
    if (
        op.get_bind()
        .execute(
            sa.text("SELECT COUNT(*) FROM runtime_attempts WHERE artifact_context_json IS NOT NULL")
        )
        .scalar()
    ):
        raise RuntimeError("v58 downgrade would erase original artifact context")
    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER runtime_attempt_artifact_context_valid")
        op.execute("DROP TRIGGER runtime_attempt_identity_immutable")
        _identity_guard(_IDENTITY)
    op.drop_column("runtime_attempts", "artifact_context_json")

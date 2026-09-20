"""v56: durable attempts and bounded execution telemetry; old retry history is unknown."""

import sqlalchemy as sa
from alembic import op

revision = "56b215ea43f0"
down_revision = "55a104d932ef"
branch_labels = None
depends_on = None

# Revision-local SQL guards, identical to metadata without importing live models.
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
_ATTEMPT_INSERT = (
    "NEW.status <> 'prepared' OR NEW.remote_run_id IS NOT NULL OR NEW.host_outcome IS NOT NULL OR "
    "NOT EXISTS (SELECT 1 FROM work_sessions w WHERE w.id=NEW.work_session_id "
    "AND w.runtime_session_ref=NEW.local_session_ref AND w.runtime_instance_id IS "
    "NEW.runtime_instance_id "
    "AND w.status='running' AND w.ended_at IS NULL) OR "
    "NEW.attempt_number <> 1 + (SELECT COALESCE(MAX(a.attempt_number), 0) FROM runtime_attempts a "
    "WHERE a.work_session_id=NEW.work_session_id AND a.request_key=NEW.request_key) OR "
    "EXISTS (SELECT 1 FROM runtime_attempts a WHERE a.work_session_id=NEW.work_session_id "
    "AND a.request_key=NEW.request_key AND (a.request_digest<>NEW.request_digest OR "
    "a.kind<>NEW.kind))"
)
GUARDS = (
    ("runtime_attempts", "runtime_attempt_insert_valid", "INSERT", _ATTEMPT_INSERT),
    (
        "runtime_attempts",
        "runtime_attempt_mock_source_valid",
        "UPDATE",
        "NEW.terminal_source = 'mock.stream' AND NOT EXISTS (SELECT 1 FROM work_sessions w "
        "WHERE w.id=NEW.work_session_id AND w.runtime_type='mock')",
    ),
    (
        "runtime_attempts",
        "runtime_attempt_identity_immutable",
        "UPDATE",
        " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in _IDENTITY),
    ),
    (
        "runtime_attempts",
        "runtime_attempt_remote_once",
        "UPDATE",
        "OLD.remote_run_id IS NOT NULL AND (NEW.remote_run_id IS NOT OLD.remote_run_id OR "
        "NEW.accepted_at IS NOT OLD.accepted_at)",
    ),
    (
        "runtime_attempts",
        "runtime_attempt_terminal_once",
        "UPDATE",
        "OLD.ended_at IS NOT NULL AND (NEW.ended_at IS NOT OLD.ended_at OR NEW.status IS NOT "
        "OLD.status OR NEW.terminal_source IS NOT OLD.terminal_source)",
    ),
    (
        "runtime_attempts",
        "runtime_attempt_host_once",
        "UPDATE",
        "OLD.host_outcome IS NOT NULL AND (NEW.host_outcome IS NOT OLD.host_outcome OR "
        "NEW.host_reason IS NOT OLD.host_reason OR NEW.host_observed_at IS NOT "
        "OLD.host_observed_at)",
    ),
    (
        "runtime_attempts",
        "runtime_attempt_progress_monotonic",
        "UPDATE",
        "NEW.last_progress_at < OLD.last_progress_at",
    ),
    (
        "runtime_attempts",
        "runtime_attempt_state_monotonic",
        "UPDATE",
        "OLD.status IN ('accepted', 'unknown') AND NEW.status = 'prepared'",
    ),
    ("runtime_attempts", "runtime_attempt_no_delete", "DELETE", "1"),
    (
        "runtime_telemetry",
        "runtime_telemetry_insert_valid",
        "INSERT",
        "NOT EXISTS (SELECT 1 FROM runtime_attempts a WHERE a.id=NEW.attempt_id AND "
        "a.work_session_id=NEW.work_session_id)",
    ),
    ("runtime_telemetry", "runtime_telemetry_immutable", "UPDATE", "1"),
    (
        "work_sessions",
        "work_session_retry_monotonic",
        "UPDATE",
        "(OLD.technical_retry_count IS NULL AND NEW.technical_retry_count IS NOT NULL) OR "
        "(OLD.technical_retry_count IS NOT NULL AND (NEW.technical_retry_count IS NULL OR "
        "NEW.technical_retry_count < OLD.technical_retry_count))",
    ),
    ("work_sessions", "work_session_retry_valid", "INSERT", "NEW.technical_retry_count < 0"),
)


def upgrade():
    op.add_column("work_sessions", sa.Column("technical_retry_count", sa.Integer(), nullable=True))
    op.create_table(
        "runtime_attempts",
        sa.Column(
            "work_session_id", sa.Integer(), sa.ForeignKey("work_sessions.id"), nullable=False
        ),
        sa.Column("request_key", sa.String(128), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("local_session_ref", sa.String(200), nullable=False),
        sa.Column("runtime_instance_id", sa.Integer(), nullable=True),
        sa.Column("instance_fingerprint", sa.String(64), nullable=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("remote_session_ref", sa.String(256), nullable=False),
        sa.Column("remote_run_id", sa.String(256), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("last_progress_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("terminal_source", sa.String(32), nullable=True),
        sa.Column("host_outcome", sa.String(16), nullable=True),
        sa.Column("host_reason", sa.String(48), nullable=True),
        sa.Column("host_observed_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("attempt_number >= 1", name="ck_runtime_attempt_number"),
        sa.CheckConstraint("kind IN ('worker', 'controlled')", name="ck_runtime_attempt_kind"),
        sa.CheckConstraint(
            "status IN ('prepared', 'accepted', 'unknown', 'completed', 'failed')",
            name="ck_runtime_attempt_status",
        ),
        sa.CheckConstraint(
            "length(request_key) BETWEEN 1 AND 128 AND length(local_session_ref) BETWEEN 1 AND "
            "200 AND length(idempotency_key) BETWEEN 1 AND 200 AND length(request_digest) = 64",
            name="ck_runtime_attempt_identity",
        ),
        sa.CheckConstraint(
            "(remote_run_id IS NULL AND accepted_at IS NULL) OR "
            "(remote_run_id IS NOT NULL AND length(remote_run_id) BETWEEN 1 AND 256 "
            "AND accepted_at IS NOT NULL)",
            name="ck_runtime_attempt_acceptance",
        ),
        sa.CheckConstraint(
            "status <> 'accepted' OR remote_run_id IS NOT NULL", name="ck_runtime_attempt_accepted"
        ),
        sa.CheckConstraint(
            "terminal_source IS NULL OR terminal_source IN ('hermes.status', 'hermes.sse', "
            "'openclaw.wait', 'host.reply', 'mock.stream')",
            name="ck_runtime_attempt_source",
        ),
        sa.CheckConstraint(
            "(status IN ('completed', 'failed') AND ended_at IS NOT NULL AND terminal_source "
            "IS NOT NULL AND (terminal_source = 'mock.stream' OR remote_run_id IS NOT NULL)) OR "
            "(status IN ('prepared', 'accepted', 'unknown') AND ended_at IS "
            "NULL AND terminal_source IS NULL)",
            name="ck_runtime_attempt_terminal",
        ),
        sa.CheckConstraint(
            "(host_outcome IS NULL AND host_reason IS NULL AND host_observed_at IS NULL) OR "
            "(host_outcome IS NOT NULL AND host_outcome IN ('completed', 'failed', 'cancelled') "
            "AND host_reason IS NOT NULL "
            "AND host_observed_at IS NOT NULL)",
            name="ck_runtime_attempt_host",
        ),
    )
    op.create_index(
        "uq_runtime_attempt_request",
        "runtime_attempts",
        ["work_session_id", "request_key", "attempt_number"],
        unique=True,
    )
    op.create_index(
        "uq_runtime_attempt_idempotency", "runtime_attempts", ["idempotency_key"], unique=True
    )
    op.create_index(
        "uq_runtime_attempt_remote",
        "runtime_attempts",
        ["runtime_instance_id", "remote_run_id"],
        unique=True,
    )
    op.create_table(
        "runtime_telemetry",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True, autoincrement=True),
        sa.Column(
            "work_session_id", sa.Integer(), sa.ForeignKey("work_sessions.id"), nullable=False
        ),
        sa.Column("attempt_id", sa.Integer(), sa.ForeignKey("runtime_attempts.id"), nullable=False),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('session.started', 'session.completed', 'session.failed', "
            "'message.completed', 'tool.called', 'tool.completed', 'tool.failed', "
            "'artifact.created', 'runtime.warning', 'runtime.error')",
            name="ck_runtime_telemetry_kind",
        ),
        sa.CheckConstraint("length(event_key) = 64", name="ck_runtime_telemetry_key"),
    )
    op.create_index(
        "uq_runtime_telemetry_event", "runtime_telemetry", ["attempt_id", "event_key"], unique=True
    )
    op.create_index(
        "ix_runtime_telemetry_page", "runtime_telemetry", ["work_session_id", "id"], unique=False
    )
    op.create_index(
        "ix_runtime_telemetry_expiry", "runtime_telemetry", ["expires_at", "id"], unique=False
    )
    if op.get_bind().dialect.name == "sqlite":
        for table, name, operation, condition in GUARDS:
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON {table} WHEN {condition} "
                "BEGIN SELECT RAISE(ABORT, 'invalid runtime execution fact'); END"
            )


def downgrade():
    # Refuse before any DDL rather than erase retained execution identity/history.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM runtime_attempts")).scalar():
        raise RuntimeError("v56 downgrade would erase durable runtime attempts")
    if op.get_bind().dialect.name == "sqlite":
        for _table, name, _operation, _condition in GUARDS:
            op.execute(f"DROP TRIGGER {name}")
    op.drop_table("runtime_telemetry")
    op.drop_table("runtime_attempts")
    op.drop_column("work_sessions", "technical_retry_count")

"""Durable execution identity and expiring observations, separate from domain facts."""

from datetime import datetime

from sqlalchemy import DDL, JSON, CheckConstraint, ForeignKey, Index, Integer, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.project import WorkSession


class RuntimeAttempt(TimestampMixin, Base):
    __tablename__ = "runtime_attempts"
    __table_args__ = (
        Index(
            "uq_runtime_attempt_request",
            "work_session_id",
            "request_key",
            "attempt_number",
            unique=True,
        ),
        Index("uq_runtime_attempt_idempotency", "idempotency_key", unique=True),
        Index("uq_runtime_attempt_remote", "runtime_instance_id", "remote_run_id", unique=True),
        CheckConstraint("attempt_number >= 1", name="ck_runtime_attempt_number"),
        CheckConstraint("kind IN ('worker', 'controlled')", name="ck_runtime_attempt_kind"),
        CheckConstraint(
            "status IN ('prepared', 'accepted', 'unknown', 'completed', 'failed')",
            name="ck_runtime_attempt_status",
        ),
        CheckConstraint(
            "length(request_key) BETWEEN 1 AND 128 AND length(local_session_ref) BETWEEN 1 AND 200 "
            "AND length(idempotency_key) BETWEEN 1 AND 200 AND length(request_digest) = 64",
            name="ck_runtime_attempt_identity",
        ),
        CheckConstraint(
            "(remote_run_id IS NULL AND accepted_at IS NULL) OR "
            "(remote_run_id IS NOT NULL AND length(remote_run_id) BETWEEN 1 AND 256 "
            "AND accepted_at IS NOT NULL)",
            name="ck_runtime_attempt_acceptance",
        ),
        CheckConstraint(
            "status <> 'accepted' OR remote_run_id IS NOT NULL", name="ck_runtime_attempt_accepted"
        ),
        CheckConstraint(
            "terminal_source IS NULL OR terminal_source IN ('hermes.status', 'hermes.sse', "
            "'openclaw.wait', 'host.reply', 'mock.stream')",
            name="ck_runtime_attempt_source",
        ),
        CheckConstraint(
            "(status IN ('completed', 'failed') AND ended_at IS NOT NULL AND terminal_source "
            "IS NOT NULL AND (terminal_source = 'mock.stream' OR remote_run_id IS NOT NULL)) OR "
            "(status IN ('prepared', 'accepted', 'unknown') AND ended_at IS NULL AND "
            "terminal_source IS NULL)",
            name="ck_runtime_attempt_terminal",
        ),
        CheckConstraint(
            "(host_outcome IS NULL AND host_reason IS NULL AND host_observed_at IS NULL) OR "
            "(host_outcome IS NOT NULL AND host_outcome IN ('completed', 'failed', 'cancelled') "
            "AND host_reason IS NOT NULL "
            "AND host_observed_at IS NOT NULL)",
            name="ck_runtime_attempt_host",
        ),
    )

    work_session_id: Mapped[int] = mapped_column(ForeignKey("work_sessions.id"))
    request_key: Mapped[str] = mapped_column(String(128))
    attempt_number: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    request_digest: Mapped[str] = mapped_column(String(64))
    local_session_ref: Mapped[str] = mapped_column(String(200))
    runtime_instance_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instance_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(String(16))
    remote_session_ref: Mapped[str] = mapped_column(String(256))
    remote_run_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    accepted_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_progress_at: Mapped[datetime] = mapped_column()
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    terminal_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    host_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    host_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    host_observed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Captured before worker dispatch; legacy, mock and controlled turns stay NULL.
    artifact_context_json: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )


class RuntimeTelemetry(Base):
    __tablename__ = "runtime_telemetry"
    __table_args__ = (
        Index("uq_runtime_telemetry_event", "attempt_id", "event_key", unique=True),
        Index("ix_runtime_telemetry_page", "work_session_id", "id"),
        Index("ix_runtime_telemetry_expiry", "expires_at", "id"),
        CheckConstraint(
            "kind IN ('session.started', 'session.completed', 'session.failed', "
            "'message.completed', "
            "'tool.called', 'tool.completed', 'tool.failed', 'artifact.created', "
            "'runtime.warning', 'runtime.error')",
            name="ck_runtime_telemetry_kind",
        ),
        CheckConstraint("length(event_key) = 64", name="ck_runtime_telemetry_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    work_session_id: Mapped[int] = mapped_column(ForeignKey("work_sessions.id"))
    attempt_id: Mapped[int] = mapped_column(ForeignKey("runtime_attempts.id"))
    event_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    details_json: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column()
    received_at: Mapped[datetime] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column()


# Match revision-local v56 guards, including direct SQL with foreign_keys=OFF.
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
    "artifact_context_json",
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
    (RuntimeAttempt.__table__, "runtime_attempt_insert_valid", "INSERT", _ATTEMPT_INSERT),
    (
        RuntimeAttempt.__table__,
        "runtime_attempt_artifact_context_valid",
        "INSERT",
        "NEW.artifact_context_json IS NOT NULL AND (NEW.kind <> 'worker' OR "
        "NEW.runtime_instance_id IS NULL OR json_valid(NEW.artifact_context_json) <> 1 OR "
        "json_type(NEW.artifact_context_json) IS NOT 'object')",
    ),
    (
        RuntimeAttempt.__table__,
        "runtime_attempt_mock_source_valid",
        "UPDATE",
        "NEW.terminal_source = 'mock.stream' AND NOT EXISTS (SELECT 1 FROM work_sessions w "
        "WHERE w.id=NEW.work_session_id AND w.runtime_type='mock')",
    ),
    (
        RuntimeAttempt.__table__,
        "runtime_attempt_identity_immutable",
        "UPDATE",
        " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in _IDENTITY),
    ),
    (
        RuntimeAttempt.__table__,
        "runtime_attempt_remote_once",
        "UPDATE",
        "OLD.remote_run_id IS NOT NULL AND (NEW.remote_run_id IS NOT OLD.remote_run_id OR "
        "NEW.accepted_at IS NOT OLD.accepted_at)",
    ),
    (
        RuntimeAttempt.__table__,
        "runtime_attempt_terminal_once",
        "UPDATE",
        "OLD.ended_at IS NOT NULL AND (NEW.ended_at IS NOT OLD.ended_at OR NEW.status IS NOT "
        "OLD.status OR NEW.terminal_source IS NOT OLD.terminal_source)",
    ),
    (
        RuntimeAttempt.__table__,
        "runtime_attempt_host_once",
        "UPDATE",
        "OLD.host_outcome IS NOT NULL AND (NEW.host_outcome IS NOT OLD.host_outcome OR "
        "NEW.host_reason IS NOT OLD.host_reason OR NEW.host_observed_at IS NOT "
        "OLD.host_observed_at)",
    ),
    (
        RuntimeAttempt.__table__,
        "runtime_attempt_progress_monotonic",
        "UPDATE",
        "NEW.last_progress_at < OLD.last_progress_at",
    ),
    (
        RuntimeAttempt.__table__,
        "runtime_attempt_state_monotonic",
        "UPDATE",
        "OLD.status IN ('accepted', 'unknown') AND NEW.status = 'prepared'",
    ),
    (RuntimeAttempt.__table__, "runtime_attempt_no_delete", "DELETE", "1"),
    (
        RuntimeTelemetry.__table__,
        "runtime_telemetry_insert_valid",
        "INSERT",
        "NOT EXISTS (SELECT 1 FROM runtime_attempts a WHERE a.id=NEW.attempt_id AND "
        "a.work_session_id=NEW.work_session_id)",
    ),
    (RuntimeTelemetry.__table__, "runtime_telemetry_immutable", "UPDATE", "1"),
    (
        WorkSession.__table__,
        "work_session_retry_monotonic",
        "UPDATE",
        "(OLD.technical_retry_count IS NULL AND NEW.technical_retry_count IS NOT NULL) OR "
        "(OLD.technical_retry_count IS NOT NULL AND (NEW.technical_retry_count IS NULL OR "
        "NEW.technical_retry_count < OLD.technical_retry_count))",
    ),
    (WorkSession.__table__, "work_session_retry_valid", "INSERT", "NEW.technical_retry_count < 0"),
)
for _table, _name, _operation, _condition in GUARDS:
    event.listen(
        _table,
        "after_create",
        DDL(
            f"CREATE TRIGGER {_name} BEFORE {_operation} ON {_table.name} WHEN {_condition} "
            "BEGIN SELECT RAISE(ABORT, 'invalid runtime execution fact'); END"
        ).execute_if(dialect="sqlite"),
    )

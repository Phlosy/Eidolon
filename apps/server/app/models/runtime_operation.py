"""Durable request identity; execution facts live only in correlated ToolAudits."""

from sqlalchemy import DDL, CheckConstraint, ForeignKey, Index, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.decision import ToolAudit


class RuntimeOperation(TimestampMixin, Base):
    __tablename__ = "runtime_operations"
    __table_args__ = (
        Index("uq_runtime_operation_key", "work_session_id", "operation_key", unique=True),
        CheckConstraint(
            "(kind = 'read' AND requested_action_count = 1) OR "
            "(kind = 'decision' AND requested_action_count BETWEEN 1 AND 16) OR "
            "(kind = 'finish' AND requested_action_count = 0)",
            name="ck_runtime_operation_kind_count",
        ),
        CheckConstraint(
            "decision_id IS NULL OR kind = 'decision'", name="ck_runtime_operation_decision"
        ),
        CheckConstraint(
            "length(operation_key) BETWEEN 1 AND 128 AND length(request_digest) = 64",
            name="ck_runtime_operation_identity_length",
        ),
    )

    work_session_id: Mapped[int] = mapped_column(ForeignKey("work_sessions.id"))
    operation_key: Mapped[str] = mapped_column(String(128))
    request_digest: Mapped[str] = mapped_column(String(64))
    # Original validated input, not a reconstructed prompt. Legacy rows stay unknown.
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(16))
    # Submitted input cardinality, never an execution aggregate.
    requested_action_count: Mapped[int] = mapped_column(Integer)
    decision_id: Mapped[int | None] = mapped_column(
        ForeignKey("decision_records.id"), nullable=True
    )


# SQLite is the supported storage engine. Match v52 in metadata-created databases;
# SQL guards cover raw/bulk writes too. Legacy uncorrelated audits remain untouched.
_IDENTITY_CHANGED = " OR ".join(
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
_VALID_RECEIPT = (
    "(NEW.runtime_operation_id IS NULL) <> (NEW.action_index IS NULL) OR "
    "(NEW.runtime_operation_id IS NOT NULL AND NOT EXISTS ("
    "SELECT 1 FROM runtime_operations op WHERE op.id = NEW.runtime_operation_id "
    "AND NEW.action_index >= 0 AND NEW.action_index < op.requested_action_count "
    "AND NEW.work_session_id = op.work_session_id "
    "AND NEW.decision_id IS op.decision_id "
    "AND (op.kind = 'read' OR (op.kind = 'decision' AND op.decision_id IS NOT NULL))))"
)
BODY_GUARDS = (
    (
        "runtime_operation_body_valid",
        "INSERT",
        "NEW.request_body IS NOT NULL AND CASE WHEN typeof(NEW.request_body) <> 'text' OR "
        "length(CAST(NEW.request_body AS BLOB)) > 65536 OR json_valid(NEW.request_body) <> 1 "
        "THEN 1 ELSE json_type(NEW.request_body) IS NOT 'object' OR "
        "json_extract(NEW.request_body, '$.version') IS NOT 1 OR "
        "json_extract(NEW.request_body, '$.kind') IS NOT NEW.kind OR "
        "json_extract(NEW.request_body, '$.operation_key') IS NOT NEW.operation_key OR "
        "(NEW.kind = 'decision' AND json_array_length(NEW.request_body, '$.decision.actions') "
        "IS NOT NEW.requested_action_count) END",
    ),
    (
        "runtime_operation_body_immutable",
        "UPDATE",
        "NEW.request_body IS NOT OLD.request_body",
    ),
    (
        "runtime_operation_no_replace",
        "INSERT",
        "EXISTS (SELECT 1 FROM runtime_operations op WHERE op.id=NEW.id OR "
        "(op.work_session_id=NEW.work_session_id AND op.operation_key=NEW.operation_key))",
    ),
)
for _table, _name, _timing, _condition, _message in (
    *(
        (RuntimeOperation.__table__, name, timing, condition, "invalid runtime request source")
        for name, timing, condition in BODY_GUARDS
    ),
    (
        RuntimeOperation.__table__,
        "runtime_operation_identity_immutable",
        "UPDATE",
        _IDENTITY_CHANGED,
        "runtime operation identity is immutable",
    ),
    (
        RuntimeOperation.__table__,
        "runtime_operation_decision_once",
        "UPDATE",
        "OLD.decision_id IS NOT NULL AND NEW.decision_id IS NOT OLD.decision_id",
        "runtime operation decision is already bound",
    ),
    (
        RuntimeOperation.__table__,
        "runtime_operation_no_delete",
        "DELETE",
        "1",
        "runtime operation identity is immutable",
    ),
    (
        ToolAudit.__table__,
        "runtime_receipt_insert_valid",
        "INSERT",
        _VALID_RECEIPT,
        "invalid runtime receipt correlation",
    ),
    (
        ToolAudit.__table__,
        "runtime_receipt_update_valid",
        "UPDATE",
        _VALID_RECEIPT,
        "invalid runtime receipt correlation",
    ),
    (
        ToolAudit.__table__,
        "runtime_receipt_immutable",
        "UPDATE",
        "OLD.runtime_operation_id IS NOT NULL",
        "runtime receipt is immutable",
    ),
    (
        ToolAudit.__table__,
        "runtime_receipt_no_delete",
        "DELETE",
        "OLD.runtime_operation_id IS NOT NULL",
        "runtime receipt is immutable",
    ),
):
    event.listen(
        _table,
        "after_create",
        DDL(
            f"CREATE TRIGGER {_name} BEFORE {_timing} ON {_table.name} WHEN {_condition} "
            f"BEGIN SELECT RAISE(ABORT, '{_message}'); END"
        ).execute_if(dialect="sqlite"),
    )

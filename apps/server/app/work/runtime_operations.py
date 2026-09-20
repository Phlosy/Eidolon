"""Trusted bridge transaction coordination, without a second execution engine."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.transaction_effects import ensure_sqlite_transaction
from app.models.decision import DecisionRecord, ToolAudit
from app.models.enums import DecisionStatus
from app.models.project import WorkSession
from app.models.runtime_operation import RuntimeOperation
from app.work import tool_executor as executor
from app.work import tools


class BridgeError(ValueError):
    """Machine-readable failure; references never imply an action was rolled back."""

    def __init__(
        self, reason: str, message: str = "", *, operation_id=None, decision_id=None
    ) -> None:
        super().__init__(message or reason)
        self.reason = reason
        self.operation_id = operation_id
        self.decision_id = decision_id

    def as_dict(self) -> dict:
        return {
            "reason": self.reason,
            "error": str(self),
            "operation_id": self.operation_id,
            "decision_id": self.decision_id,
        }


def receipt_result(db: Session, audit: ToolAudit) -> executor.ToolResult:
    """Rehydrate the whole original result from its single durable fact source."""
    session = db.get(WorkSession, audit.work_session_id, populate_existing=True)
    if session is None:
        raise BridgeError("receipt_identity_unavailable", operation_id=audit.runtime_operation_id)
    return executor.ToolResult(
        ok=audit.outcome in {"read", "applied"},
        tool=audit.tool_name,
        side_effect=audit.side_effect,
        autonomy=audit.autonomy,
        transport=audit.transport,
        data=audit.result_json or {},
        reason="ok" if audit.outcome == "read" else audit.outcome,
        error=audit.error,
        authority=audit.authority_json,
        audit_id=audit.id,
        actor=tools.ToolCallContext(
            employee_id=audit.actor_employee_id,
            person_id=audit.actor_person_id,
            company_id=audit.actor_company_id,
            origin=audit.origin,
            work_session_id=audit.work_session_id,
            task_id=audit.task_id,
            project_id=audit.project_id,
            runtime_type=session.runtime_type,
            purpose=session.purpose,
            review_request_id=session.review_request_id,
        ).as_dict(),
    )


@dataclass(frozen=True)
class RuntimeExecution:
    """Internal-only correlation passed to submit_envelope, never from model JSON.

    The bridge holds the initial writer transaction while creating/binding intent.
    Each action then owns a new, short session. A missing receipt is safe to apply
    because correlation, ToolAudit, effect and source Event commit together.
    """

    operation_id: int
    work_session_id: int
    request_digest: str
    session_factory: Callable[[], Session]
    validate_references: Callable[[Session, tools.ToolCallContext], None] | None = None

    def operation(self, db: Session) -> RuntimeOperation:
        op = db.get(RuntimeOperation, self.operation_id, populate_existing=True)
        if (
            op is None
            or op.work_session_id != self.work_session_id
            or op.request_digest != self.request_digest
        ):
            raise BridgeError("operation_identity_mismatch", operation_id=self.operation_id)
        return op

    def bound_decision(self, db: Session, ctx: tools.ToolCallContext) -> DecisionRecord | None:
        op = self.operation(db)
        if op.kind != "decision" or ctx.work_session_id != self.work_session_id:
            raise BridgeError("operation_identity_mismatch", operation_id=op.id)
        if op.decision_id is None:
            return None
        decision = db.get(DecisionRecord, op.decision_id, populate_existing=True)
        if (
            decision is None
            or decision.company_id != ctx.company_id
            or decision.actor_employee_id != ctx.employee_id
            or decision.actor_person_id != ctx.person_id
        ):
            raise BridgeError(
                "decision_identity_mismatch", operation_id=op.id, decision_id=op.decision_id
            )
        return decision

    def bind_decision(self, db: Session, decision: DecisionRecord, action_count: int) -> None:
        op = self.operation(db)
        if op.requested_action_count != action_count:
            raise BridgeError("operation_identity_mismatch", operation_id=op.id)
        if op.decision_id is None:
            op.decision_id = decision.id
            decision.status = DecisionStatus.executing.value
        elif op.decision_id != decision.id:
            raise BridgeError("decision_identity_mismatch", operation_id=op.id)
        # The new operation, intent and once-only binding share one commit.
        db.commit()

    def execute_action(self, index: int, action: dict, *, decision_id=None) -> executor.ToolResult:
        with self.session_factory() as db:
            # Must precede every identity / authority / receipt read. This also
            # serializes competing callers before SQLite's read-to-write upgrade.
            ensure_sqlite_transaction(db)
            try:
                ctx = executor.context_for_work_session(db, self.work_session_id)
                if self.validate_references is not None:
                    self.validate_references(db, ctx)
            except executor.ToolExecutionError as exc:
                raise BridgeError(
                    exc.reason, str(exc), operation_id=self.operation_id, decision_id=decision_id
                ) from exc
            except tools.ToolError as exc:
                raise BridgeError(
                    "invalid_reference",
                    str(exc),
                    operation_id=self.operation_id,
                    decision_id=decision_id,
                ) from exc
            op = self.operation(db)
            if op.decision_id != decision_id or not 0 <= index < op.requested_action_count:
                raise BridgeError("operation_identity_mismatch", operation_id=op.id)
            audit = db.scalar(
                select(ToolAudit).where(
                    ToolAudit.runtime_operation_id == op.id, ToolAudit.action_index == index
                )
            )
            if audit is not None:
                if audit.tool_name != action["tool"] or audit.arguments_json != action["args"]:
                    raise BridgeError("receipt_mismatch", operation_id=op.id)
                return receipt_result(db, audit)
            if decision_id is not None:
                decision = self.bound_decision(db, ctx)
                if (
                    decision.status != DecisionStatus.executing.value
                    or decision.resolved_at is not None
                    or decision.superseded_by_id is not None
                ):
                    raise BridgeError(
                        "decision_not_resumable", operation_id=op.id, decision_id=decision_id
                    )
            result = executor.execute_tool(
                db,
                name=action["tool"],
                args=action["args"],
                context=ctx,
                decision_id=decision_id,
                commit=False,
                _runtime_action=(op.id, index),
            )
            audit = db.get(ToolAudit, result.audit_id) if result.audit_id is not None else None
            if audit is None:
                raise BridgeError("receipt_missing", operation_id=op.id, decision_id=decision_id)
            audit.runtime_operation_id = op.id
            audit.action_index = index
            db.flush()
            if audit.outcome == "confirmation_pending":
                from app.work.action_confirmations import attach_proposal_receipt

                attach_proposal_receipt(db, audit)
            # Always use the persisted JSON representation, including on first
            # delivery (handlers may return datetimes or other domain values).
            saved = receipt_result(db, audit)
            db.commit()
            return saved

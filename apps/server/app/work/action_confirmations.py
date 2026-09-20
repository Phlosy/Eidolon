"""Durable, fixed-action human confirmation. No model-accessible approval path."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.transaction_effects import ensure_sqlite_transaction
from app.models.action_confirmation import ActionConfirmation
from app.models.auth import CompanyMembership, User, UserAuditEvent, UserSession
from app.models.base import as_utc, utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.runtime_operation import RuntimeOperation
from app.work import tools

TERMINAL = frozenset({"executed", "failed", "rejected", "expired"})


class ConfirmationError(tools.ToolError):
    def __init__(self, reason: str, *, status_code: int = 409):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def require_owner(db, *, company_id, user_id, session_id):
    """Fresh stored identity; never resolve cookie auth inside an effect transaction."""
    user = db.get(User, user_id, populate_existing=True)
    session = db.get(UserSession, session_id, populate_existing=True)
    if (
        user is None
        or user.status != "active"
        or session is None
        or session.user_id != user_id
        or session.revoked_at is not None
        or as_utc(session.expires_at) <= utcnow()
    ):
        raise ConfirmationError("authentication_required", status_code=401)
    membership = db.scalar(
        select(CompanyMembership)
        .where(
            CompanyMembership.user_id == user_id,
            CompanyMembership.company_id == company_id,
        )
        .execution_options(populate_existing=True)
    )
    if membership is None or membership.role != "OWNER":
        raise ConfirmationError("company_owner_required", status_code=403)


def _binding(db, ctx, decision_id, runtime_action, spec):
    if (
        ctx.purpose != "management"
        or ctx.origin != "work_session"
        or ctx.work_session_id is None
        or ctx.person_id is None
        or not runtime_action
    ):
        raise ConfirmationError("confirmation_requires_management_envelope")
    operation_id, index = runtime_action
    op = db.get(RuntimeOperation, operation_id, populate_existing=True)
    decision = db.get(DecisionRecord, decision_id, populate_existing=True)
    if (
        op is None
        or op.kind != "decision"
        or op.work_session_id != ctx.work_session_id
        or op.decision_id != decision_id
        or type(index) is not int
        or not 0 <= index < op.requested_action_count
        or decision is None
        or decision.actor_employee_id != ctx.employee_id
        or decision.actor_person_id != ctx.person_id
        or decision.company_id != ctx.company_id
        or decision.project_id != ctx.project_id
        or decision.status != "EXECUTING"
        or decision.resolved_at is not None
        or decision.superseded_by_id is not None
        or decision.decision_type != spec.confirmation_policy.decision_kind.value
    ):
        raise ConfirmationError("confirmation_binding_invalid")
    return op, index


def _terms(db, spec, ctx, args):
    terms = spec.confirmation_policy.resolve_terms(db, ctx, args)
    if not isinstance(terms, tools.ConfirmationTerms):
        raise ConfirmationError("confirmation_terms_invalid")
    if type(args.get(spec.amount_arg)) is not int or args[spec.amount_arg] != terms.amount:
        raise ConfirmationError("confirmation_amount_mismatch")
    return terms


def propose(db, *, spec, ctx, args, decision_id, runtime_action):
    from app.work.tool_executor import _args_digest

    op, index = _binding(db, ctx, decision_id, runtime_action, spec)
    if args.get("project_id") != ctx.project_id:
        raise ConfirmationError("confirmation_binding_invalid")
    terms = _terms(db, spec, ctx, args)
    row = ActionConfirmation(
        company_id=ctx.company_id,
        employee_id=ctx.employee_id,
        person_id=ctx.person_id,
        project_id=ctx.project_id,
        work_session_id=ctx.work_session_id,
        decision_id=decision_id,
        runtime_operation_id=op.id,
        action_index=index,
        tool_name=spec.name,
        policy_version=spec.confirmation_policy.version,
        arguments_json=tools.json_safe(args),
        arguments_digest=_args_digest(args),
        target_ref=terms.target_ref,
        target_version=terms.target_version,
        amount=terms.amount,
        expires_at=utcnow() + timedelta(seconds=spec.confirmation_policy.expires_in_seconds),
        status="pending",
    )
    db.add(row)
    db.flush()
    return row


def attach_proposal_receipt(db, audit):
    ref = (audit.result_json or {}).get("confirmation", {})
    row = db.get(ActionConfirmation, ref.get("id"))
    if (
        row is None
        or row.runtime_operation_id != audit.runtime_operation_id
        or row.action_index != audit.action_index
        or row.proposal_audit_id is not None
    ):
        raise ConfirmationError("confirmation_receipt_invalid")
    row.proposal_audit_id = audit.id
    db.flush()


def proposal_ref(row):
    return {
        "id": row.id,
        "status": row.status,
        "arguments_digest": row.arguments_digest,
        "target_ref": row.target_ref,
        "target_version": row.target_version,
        "amount": row.amount,
        "expires_at": as_utc(row.expires_at).isoformat(),
    }


def get_for_company(db, company_id, confirmation_id):
    row = db.get(ActionConfirmation, confirmation_id, populate_existing=True)
    if row is None or row.company_id != company_id:
        raise ConfirmationError("confirmation_not_found", status_code=404)
    return row


def view(db, row):
    final = db.get(ToolAudit, row.final_audit_id) if row.final_audit_id is not None else None
    return {
        **proposal_ref(row),
        "company_id": row.company_id,
        "employee_id": row.employee_id,
        "person_id": row.person_id,
        "project_id": row.project_id,
        "work_session_id": row.work_session_id,
        "decision_id": row.decision_id,
        "runtime_operation_id": row.runtime_operation_id,
        "action_index": row.action_index,
        "tool_name": row.tool_name,
        "policy_version": row.policy_version,
        "arguments": row.arguments_json,
        "proposal_audit_id": row.proposal_audit_id,
        "final_audit_id": row.final_audit_id,
        "review_outcome": row.review_outcome,
        "reviewer_user_id": row.reviewer_user_id,
        "reviewed_at": as_utc(row.reviewed_at),
        "resolved_at": as_utc(row.resolved_at),
        "created_at": as_utc(row.created_at),
        "deadline_passed": as_utc(row.expires_at) <= utcnow(),
        "result": None
        if final is None
        else {
            "ok": final.outcome == "applied",
            "reason": final.outcome,
            "data": final.result_json,
            "error": final.error,
        },
    }


def list_for_company(db, company_id, *, status=None, project_id=None, after_id=0, limit=50):
    stmt = select(ActionConfirmation).where(
        ActionConfirmation.company_id == company_id,
        ActionConfirmation.id > max(0, after_id),
    )
    if status is not None:
        stmt = stmt.where(ActionConfirmation.status == status)
    if project_id is not None:
        stmt = stmt.where(ActionConfirmation.project_id == project_id)
    return [
        view(db, row)
        for row in db.scalars(
            stmt.order_by(ActionConfirmation.id)
            .limit(max(1, min(limit, 100)))
            .execution_options(populate_existing=True)
        )
    ]


def _refresh_decision(db, row):
    from app.work.decisions import refresh_from_receipts

    refresh_from_receipts(db, row.decision_id)


def _finish_without_effect(db, row, status, reason):
    """Record a denial without requiring a still-installed tool or live proposer."""
    original = db.get(ToolAudit, row.proposal_audit_id)
    if original is None:
        raise ConfirmationError("confirmation_receipt_invalid")
    values = {
        column.name: getattr(original, column.name)
        for column in ToolAudit.__table__.columns
        if column.name
        not in {
            "id",
            "created_at",
            "updated_at",
            "runtime_operation_id",
            "action_index",
            "outcome",
            "result_json",
            "error",
            "started_at",
            "finished_at",
            "authority_json",
            "authority_allowed",
            "authority_reason",
            "authority_grant_ids",
            "authority_grants_hash",
        }
    }
    audit = ToolAudit(
        **values,
        outcome=reason,
        result_json={"confirmation_id": row.id},
        error=reason,
        # No current Authority check ran on this path. The original snapshot
        # remains on the proposal receipt, rather than being relabelled now.
        authority_json=None,
        authority_allowed=None,
        started_at=utcnow(),
        finished_at=utcnow(),
    )
    db.add(audit)
    db.flush([audit])
    row.status = status
    row.final_audit_id = audit.id
    row.resolved_at = utcnow()
    db.flush()
    _refresh_decision(db, row)


def _expired(db, row):
    if row.status not in TERMINAL and as_utc(row.expires_at) <= utcnow():
        _finish_without_effect(db, row, "expired", "confirmation_expired")
        return True
    return False


def review(db: Session, confirmation_id, *, company_id, user_id, session_id, approve):
    """Commit the human decision independently, so approved work survives restart."""
    ensure_sqlite_transaction(db)
    require_owner(db, company_id=company_id, user_id=user_id, session_id=session_id)
    row = get_for_company(db, company_id, confirmation_id)
    if _expired(db, row):
        db.commit()
        return row
    if row.status == "pending":
        row.review_outcome = "approved" if approve else "rejected"
        row.reviewer_user_id = user_id
        row.reviewer_session_id = session_id
        row.reviewed_at = utcnow()
        if approve:
            row.status = "approved"
        else:
            # Fill the whole review+terminal tuple before SQL constraints see it.
            with db.no_autoflush:
                _finish_without_effect(db, row, "rejected", "confirmation_rejected")
        db.add(
            UserAuditEvent(
                user_id=user_id,
                company_id=company_id,
                action=f"action_confirmation.{row.review_outcome}",
                metadata_json={
                    "confirmation_id": row.id,
                    "session_id": session_id,
                    "arguments_digest": row.arguments_digest,
                },
            )
        )
        db.flush()
    elif (approve and row.status == "rejected") or (not approve and row.status != "rejected"):
        raise ConfirmationError("confirmation_already_reviewed")
    db.commit()
    return row


def validate_execution(db, row, *, spec, ctx, args, decision_id):
    from app.work.tool_executor import _args_digest

    saved = db.get(ActionConfirmation, row.id, populate_existing=True)
    if (
        saved is None
        or saved.status != "approved"
        or saved.review_outcome != "approved"
        or saved.proposal_audit_id is None
        or as_utc(saved.expires_at) <= utcnow()
        or saved.tool_name != spec.name
        or saved.policy_version != spec.confirmation_policy.version
        or saved.company_id != ctx.company_id
        or saved.employee_id != ctx.employee_id
        or saved.person_id != ctx.person_id
        or saved.project_id != ctx.project_id
        or saved.work_session_id != ctx.work_session_id
        or saved.decision_id != decision_id
        or saved.arguments_json != args
        or saved.arguments_digest != _args_digest(args)
    ):
        raise ConfirmationError("confirmation_binding_invalid")
    try:
        require_owner(
            db,
            company_id=saved.company_id,
            user_id=saved.reviewer_user_id,
            session_id=saved.reviewer_session_id,
        )
    except ConfirmationError as exc:
        raise ConfirmationError("confirmation_approval_revoked") from exc
    _binding(db, ctx, decision_id, (saved.runtime_operation_id, saved.action_index), spec)
    terms = _terms(db, spec, ctx, args)
    if (terms.target_ref, terms.target_version, terms.amount) != (
        saved.target_ref,
        saved.target_version,
        saved.amount,
    ):
        raise ConfirmationError("confirmation_terms_changed")


def execute(db: Session, confirmation_id, *, company_id, user_id, session_id):
    """Effect + final receipt + terminal state in one short writer transaction."""
    from app.work import tool_executor as executor

    ensure_sqlite_transaction(db)
    require_owner(db, company_id=company_id, user_id=user_id, session_id=session_id)
    row = get_for_company(db, company_id, confirmation_id)
    if row.status in TERMINAL:
        db.commit()
        return row
    if _expired(db, row):
        db.commit()
        return row
    if row.status != "approved":
        raise ConfirmationError("confirmation_not_approved")
    try:
        ctx = executor._context_for_work_session(db, row.work_session_id, require_running=False)
    except executor.ToolExecutionError as exc:
        _finish_without_effect(db, row, "failed", exc.reason)
    else:
        # A savepoint also covers the executor's receipt and our link. A storage
        # failure is not a business rejection and must leave approval retryable.
        with db.begin_nested():
            result = executor.execute_tool(
                db,
                name=row.tool_name,
                args=dict(row.arguments_json),
                context=ctx,
                decision_id=row.decision_id,
                commit=False,
                _confirmation=row,
            )
            if result.audit_id is None:
                _finish_without_effect(db, row, "failed", result.reason)
            else:
                row.status = "executed" if result.ok else "failed"
                row.final_audit_id = result.audit_id
                row.resolved_at = utcnow()
                db.flush()
                _refresh_decision(db, row)
    db.commit()
    return row


def expire_due(db, *, limit=100):
    """Bounded durable expiry; queries only expose facts and never mutate them."""
    ensure_sqlite_transaction(db)
    rows = list(
        db.scalars(
            select(ActionConfirmation)
            .where(
                ActionConfirmation.status.in_(("pending", "approved")),
                ActionConfirmation.expires_at <= utcnow(),
            )
            .order_by(ActionConfirmation.id)
            .limit(max(1, min(limit, 100)))
            .execution_options(populate_existing=True)
        )
    )
    for row in rows:
        _expired(db, row)
    db.commit()
    return len(rows)


def expire(db, confirmation_id, *, company_id, user_id, session_id):
    ensure_sqlite_transaction(db)
    require_owner(db, company_id=company_id, user_id=user_id, session_id=session_id)
    row = get_for_company(db, company_id, confirmation_id)
    if row.status not in TERMINAL and not _expired(db, row):
        raise ConfirmationError("confirmation_not_expired")
    db.commit()
    return row

"""Fixed human authorization and once-only links to immutable action receipts."""

from datetime import datetime

from sqlalchemy import DDL, JSON, CheckConstraint, ForeignKey, Index, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.decision import ToolAudit


class ActionConfirmation(TimestampMixin, Base):
    __tablename__ = "action_confirmations"
    __table_args__ = (
        Index("uq_confirmation_action", "runtime_operation_id", "action_index", unique=True),
        Index("ix_confirmation_company_status", "company_id", "status", "id"),
        CheckConstraint(
            "amount >= 0 AND length(arguments_digest) = 64 AND action_index >= 0 "
            "AND length(target_ref) BETWEEN 1 AND 256 "
            "AND length(target_version) BETWEEN 1 AND 256 "
            "AND length(policy_version) BETWEEN 1 AND 64 AND expires_at > created_at",
            name="ck_confirmation_terms",
        ),
        CheckConstraint(
            "(review_outcome IS NULL AND reviewed_at IS NULL AND reviewer_user_id IS NULL "
            "AND reviewer_session_id IS NULL) OR (review_outcome IS NOT NULL "
            "AND review_outcome IN ('approved', 'rejected') AND reviewed_at IS NOT NULL "
            "AND reviewer_user_id IS NOT NULL AND reviewer_session_id IS NOT NULL)",
            name="ck_confirmation_review",
        ),
        CheckConstraint(
            "(status = 'pending' AND review_outcome IS NULL AND resolved_at IS NULL "
            "AND final_audit_id IS NULL) OR "
            "(status = 'approved' AND review_outcome IS NOT NULL AND review_outcome = 'approved' "
            "AND resolved_at IS NULL AND final_audit_id IS NULL "
            "AND proposal_audit_id IS NOT NULL) OR "
            "(status IN ('executed', 'failed', 'rejected', 'expired') "
            "AND resolved_at IS NOT NULL AND final_audit_id IS NOT NULL "
            "AND proposal_audit_id IS NOT NULL "
            "AND (status = 'expired' OR (review_outcome IS NOT NULL AND "
            "((status IN ('executed', 'failed') AND review_outcome = 'approved') "
            "OR (status = 'rejected' AND review_outcome = 'rejected')))))",
            name="ck_confirmation_state",
        ),
    )

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    work_session_id: Mapped[int] = mapped_column(ForeignKey("work_sessions.id"))
    decision_id: Mapped[int] = mapped_column(ForeignKey("decision_records.id"))
    runtime_operation_id: Mapped[int] = mapped_column(ForeignKey("runtime_operations.id"))
    action_index: Mapped[int]
    tool_name: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[str] = mapped_column(String(64))
    arguments_json: Mapped[dict] = mapped_column(JSON)
    arguments_digest: Mapped[str] = mapped_column(String(64))
    target_ref: Mapped[str] = mapped_column(String(256))
    target_version: Mapped[str] = mapped_column(String(256))
    amount: Mapped[int]
    expires_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16), default="pending")
    proposal_audit_id: Mapped[int | None] = mapped_column(ForeignKey("tool_audits.id"), unique=True)
    final_audit_id: Mapped[int | None] = mapped_column(ForeignKey("tool_audits.id"), unique=True)
    review_outcome: Mapped[str | None] = mapped_column(String(16))
    reviewer_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewer_session_id: Mapped[int | None] = mapped_column(ForeignKey("user_sessions.id"))
    reviewed_at: Mapped[datetime | None]
    resolved_at: Mapped[datetime | None]


_IDENTITY = (
    "id",
    "created_at",
    "company_id",
    "employee_id",
    "person_id",
    "project_id",
    "work_session_id",
    "decision_id",
    "runtime_operation_id",
    "action_index",
    "tool_name",
    "policy_version",
    "arguments_json",
    "arguments_digest",
    "target_ref",
    "target_version",
    "amount",
    "expires_at",
)
_REVIEW = ("review_outcome", "reviewer_user_id", "reviewer_session_id", "reviewed_at")
_CHANGED = " OR ".join(f"NEW.{key} IS NOT OLD.{key}" for key in _IDENTITY)
_REVIEW_CHANGED = " OR ".join(f"NEW.{key} IS NOT OLD.{key}" for key in _REVIEW)
_RECEIPTS_VALID = (
    "(NEW.proposal_audit_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tool_audits a "
    "WHERE a.id = NEW.proposal_audit_id AND a.runtime_operation_id = NEW.runtime_operation_id "
    "AND a.action_index = NEW.action_index AND a.decision_id = NEW.decision_id "
    "AND a.arguments_digest = NEW.arguments_digest AND a.tool_name = NEW.tool_name "
    "AND a.actor_employee_id = NEW.employee_id AND a.actor_person_id = NEW.person_id "
    "AND a.actor_company_id = NEW.company_id AND a.work_session_id = NEW.work_session_id "
    "AND a.outcome = 'confirmation_pending')) OR "
    "(NEW.final_audit_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM tool_audits a "
    "WHERE a.id = NEW.final_audit_id AND a.runtime_operation_id IS NULL "
    "AND a.action_index IS NULL AND a.decision_id = NEW.decision_id "
    "AND a.arguments_digest = NEW.arguments_digest AND a.tool_name = NEW.tool_name "
    "AND a.actor_employee_id = NEW.employee_id AND a.actor_person_id = NEW.person_id "
    "AND a.actor_company_id = NEW.company_id AND a.work_session_id = NEW.work_session_id "
    "AND ((NEW.status = 'executed' AND a.outcome = 'applied') "
    "OR (NEW.status = 'rejected' AND a.outcome = 'confirmation_rejected') "
    "OR (NEW.status = 'expired' AND a.outcome = 'confirmation_expired') "
    "OR (NEW.status = 'failed' AND a.outcome NOT IN ('applied', 'confirmation_pending', 'read')))))"
)

# Shared SQL for metadata-created test databases; v57 keeps its own frozen copy.
GUARDS = (
    (
        "confirmation_insert_valid",
        "INSERT",
        "NEW.status <> 'pending' OR NEW.review_outcome IS NOT NULL OR "
        "NEW.proposal_audit_id IS NOT NULL OR NEW.final_audit_id IS NOT NULL OR "
        "NOT EXISTS (SELECT 1 FROM runtime_operations o "
        "JOIN work_sessions w ON w.id = o.work_session_id "
        "JOIN decision_records d ON d.id = o.decision_id "
        "WHERE o.id = NEW.runtime_operation_id AND o.kind = 'decision' "
        "AND NEW.action_index < o.requested_action_count AND NEW.decision_id = d.id "
        "AND NEW.work_session_id = w.id AND w.purpose = 'management' "
        "AND w.status = 'running' AND w.ended_at IS NULL "
        "AND NEW.company_id = w.company_id AND NEW.company_id = d.company_id "
        "AND NEW.employee_id = w.employee_id AND NEW.employee_id = d.actor_employee_id "
        "AND NEW.person_id = w.person_id AND NEW.person_id = d.actor_person_id "
        "AND NEW.project_id = w.project_id AND NEW.project_id = d.project_id "
        "AND d.status = 'EXECUTING' AND d.resolved_at IS NULL AND d.superseded_by_id IS NULL)",
    ),
    ("confirmation_identity_immutable", "UPDATE", _CHANGED),
    (
        "confirmation_review_once",
        "UPDATE",
        f"OLD.review_outcome IS NOT NULL AND ({_REVIEW_CHANGED})",
    ),
    (
        "confirmation_proposal_once",
        "UPDATE",
        "OLD.proposal_audit_id IS NOT NULL AND NEW.proposal_audit_id IS NOT OLD.proposal_audit_id",
    ),
    ("confirmation_terminal_immutable", "UPDATE", "OLD.resolved_at IS NOT NULL"),
    ("confirmation_no_delete", "DELETE", "1"),
    ("confirmation_receipts_insert_valid", "INSERT", _RECEIPTS_VALID),
    ("confirmation_receipts_update_valid", "UPDATE", _RECEIPTS_VALID),
    (
        "confirmation_transition_valid",
        "UPDATE",
        "NOT ((OLD.status = 'pending' AND NEW.status IN "
        "('pending', 'approved', 'rejected', 'expired')) OR (OLD.status = 'approved' "
        "AND NEW.status IN ('approved', 'executed', 'failed', 'expired')))",
    ),
)
for _name, _timing, _condition in GUARDS:
    event.listen(
        ActionConfirmation.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER {_name} BEFORE {_timing} ON action_confirmations WHEN {_condition} "
            "BEGIN SELECT RAISE(ABORT, 'invalid or immutable action confirmation'); END"
        ).execute_if(dialect="sqlite"),
    )

for _timing in ("UPDATE", "DELETE"):
    event.listen(
        ToolAudit.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER confirmation_final_receipt_{_timing.lower()} BEFORE {_timing} "
            "ON tool_audits WHEN EXISTS (SELECT 1 FROM action_confirmations c "
            "WHERE c.final_audit_id = OLD.id) "
            "BEGIN SELECT RAISE(ABORT, 'confirmation final receipt is immutable'); END"
        ).execute_if(dialect="sqlite"),
    )

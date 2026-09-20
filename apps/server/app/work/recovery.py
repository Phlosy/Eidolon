"""Bounded recovery of committed facts and approved actions; no Runtime/model calls."""

from dataclasses import dataclass

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.request_context import get_request_identity
from app.models.action_confirmation import ActionConfirmation
from app.models.decision import DecisionRecord, ToolAudit
from app.models.enums import DecisionStatus
from app.models.project import Project, WorkSession
from app.models.runtime_artifact import RuntimeArtifactCollection
from app.models.runtime_execution import RuntimeAttempt
from app.models.runtime_operation import RuntimeOperation
from app.work import action_confirmations, decisions, project_completion
from app.work.dispatch_notices import begin_observation
from app.work.telemetry import record_host_outcome

MAX_BATCH = 100


@dataclass(frozen=True)
class RecoveryObservation:
    work_session_id: int
    attempt_id: int | None
    outcome: str


@dataclass(frozen=True)
class RecoveryBatch:
    cursor: int
    complete: bool
    items: tuple[RecoveryObservation, ...]


@dataclass(frozen=True)
class ActionRecoveryObservation:
    confirmation_id: int
    outcome: str
    final_audit_id: int | None


@dataclass(frozen=True)
class ActionRecoveryBatch:
    cursor: int
    complete: bool
    items: tuple[ActionRecoveryObservation, ...]


@dataclass(frozen=True)
class DecisionRecoveryObservation:
    decision_id: int
    outcome: str
    status: str | None


@dataclass(frozen=True)
class DecisionRecoveryBatch:
    cursor: int
    complete: bool
    items: tuple[DecisionRecoveryObservation, ...]


@dataclass(frozen=True)
class ProjectRecoveryBatch:
    cursor: int
    complete: bool
    items: tuple[project_completion.CompletionObservation, ...]


def _scoped(query, model=WorkSession):
    identity = get_request_identity()
    return query.where(model.company_id == identity.company_id) if identity else query


def _validate_page(through_id, after_id, limit):
    if (
        type(limit) is not int
        or not 1 <= limit <= MAX_BATCH
        or type(after_id) is not int
        or type(through_id) is not int
        or not 0 <= after_id <= through_id
    ):
        raise ValueError("invalid recovery page")


def work_session_boundary(*, session_factory=SessionLocal):
    """Capture a finite startup cohort; a later worker belongs to its live owner."""
    with session_factory() as db:
        return db.scalar(_scoped(select(func.max(WorkSession.id)))) or 0


def running_workers(*, through_id, after_id=0, limit=25, session_factory=SessionLocal):
    """Read a bounded startup cohort; the orchestrator retains live ownership."""
    _validate_page(through_id, after_id, limit)
    with session_factory() as db:
        ids = list(
            db.scalars(
                _scoped(
                    select(WorkSession.id)
                    .where(
                        WorkSession.id > after_id,
                        WorkSession.id <= through_id,
                        WorkSession.purpose == "worker",
                        WorkSession.runtime_type.in_(("hermes", "openclaw")),
                        WorkSession.status == "running",
                        WorkSession.ended_at.is_(None),
                    )
                    .order_by(WorkSession.id)
                    .limit(limit)
                )
            )
        )
    cursor = ids[-1] if ids else through_id
    return RecoveryBatch(
        cursor,
        len(ids) < limit or cursor == through_id,
        tuple(RecoveryObservation(identifier, None, "needs_recovery") for identifier in ids),
    )


def running_controlled(*, through_id, after_id=0, limit=25, session_factory=SessionLocal):
    _validate_page(through_id, after_id, limit)
    with session_factory() as db:
        ids = list(
            db.scalars(
                _scoped(
                    select(WorkSession.id)
                    .where(
                        WorkSession.id > after_id,
                        WorkSession.id <= through_id,
                        WorkSession.purpose.in_(("management", "review")),
                        WorkSession.task_id.is_(None),
                        WorkSession.runtime_type.in_(("hermes", "openclaw")),
                        WorkSession.status == "running",
                        WorkSession.ended_at.is_(None),
                    )
                    .order_by(WorkSession.id)
                    .limit(limit)
                )
            )
        )
    cursor = ids[-1] if ids else through_id
    return RecoveryBatch(
        cursor,
        len(ids) < limit or cursor == through_id,
        tuple(RecoveryObservation(identifier, None, "needs_recovery") for identifier in ids),
    )


def _observe(db, work):
    if work.purpose != "worker" or work.runtime_type not in {"hermes", "openclaw"}:
        return RecoveryObservation(work.id, None, "not_real_worker")
    if work.status != "completed" or work.ended_at is None:
        return RecoveryObservation(work.id, None, "not_completed")
    attempt = db.scalar(
        select(RuntimeAttempt)
        .join(RuntimeArtifactCollection, RuntimeArtifactCollection.attempt_id == RuntimeAttempt.id)
        .where(RuntimeAttempt.work_session_id == work.id)
        .limit(1)
    )
    if attempt is None:
        return RecoveryObservation(work.id, None, "no_collection")
    if attempt.status != "completed" or attempt.ended_at is None:
        return RecoveryObservation(work.id, attempt.id, "source_incomplete")
    return RecoveryObservation(
        work.id,
        attempt.id,
        "already_recorded" if attempt.host_outcome is not None else "needs_repair",
    )


def reconcile_committed_workers(*, through_id, after_id=0, limit=25, session_factory=SessionLocal):
    """Inspect one primary-key page and fill only proven, missing host outcomes.

    An accepted collection alone does not finalize a WorkSession. The source is
    rechecked under the same short write transaction as the host observation.
    This does not inspect current employment, query a Runtime, or repeat effects.
    """
    _validate_page(through_id, after_id, limit)
    with session_factory() as db:
        work = list(
            db.scalars(
                _scoped(
                    select(WorkSession)
                    .where(WorkSession.id > after_id, WorkSession.id <= through_id)
                    .order_by(WorkSession.id)
                    .limit(limit)
                )
            )
        )
        observations = [_observe(db, row) for row in work]
    # Most pages contain no missing observation; never take a writer lock just
    # to skip live, failed, legacy, or already recorded work.
    results = []
    for observed in observations:
        if observed.outcome != "needs_repair":
            results.append(observed)
            continue
        with session_factory() as db:
            begin_observation(db)
            current = db.scalar(
                _scoped(select(WorkSession).where(WorkSession.id == observed.work_session_id))
            )
            if current is None:
                results.append(
                    RecoveryObservation(observed.work_session_id, None, "source_changed")
                )
            else:
                checked = _observe(db, current)
                if checked.outcome == "needs_repair":
                    record_host_outcome(
                        db, checked.attempt_id, "completed", "domain_result_recovered"
                    )
                    checked = RecoveryObservation(current.id, checked.attempt_id, "repaired")
                results.append(checked)
            db.commit()
    cursor = observations[-1].work_session_id if observations else through_id
    return RecoveryBatch(cursor, len(observations) < limit or cursor == through_id, tuple(results))


def confirmation_boundary(*, session_factory=SessionLocal):
    with session_factory() as db:
        return db.scalar(_scoped(select(func.max(ActionConfirmation.id)), ActionConfirmation)) or 0


def _observe_action(row):
    if row.status == "approved":
        outcome = "needs_recovery"
    elif row.status in action_confirmations.TERMINAL:
        outcome = "already_resolved"
    else:
        outcome = "not_approved"
    return ActionRecoveryObservation(row.id, outcome, row.final_audit_id)


def reconcile_approved_actions(*, through_id, after_id=0, limit=25, session_factory=SessionLocal):
    """Continue only a saved approval, using its original live Owner authorization.

    The existing executor owns identity/terms/authority checks and the atomic
    effect/receipt transaction. Storage failures leave the caller's cursor in
    place; replay reuses any action that committed before the failure.
    """
    _validate_page(through_id, after_id, limit)
    with session_factory() as db:
        rows = db.execute(
            _scoped(
                select(
                    ActionConfirmation.id,
                    ActionConfirmation.status,
                    ActionConfirmation.final_audit_id,
                )
                .where(ActionConfirmation.id > after_id, ActionConfirmation.id <= through_id)
                .order_by(ActionConfirmation.id)
                .limit(limit),
                ActionConfirmation,
            )
        )
        observations = [_observe_action(row) for row in rows]
    results = []
    for observed in observations:
        if observed.outcome != "needs_recovery":
            results.append(observed)
            continue
        with session_factory() as db:
            begin_observation(db)
            current = db.scalar(
                _scoped(
                    select(ActionConfirmation).where(
                        ActionConfirmation.id == observed.confirmation_id
                    ),
                    ActionConfirmation,
                )
            )
            checked = (
                _observe_action(current)
                if current is not None
                else ActionRecoveryObservation(observed.confirmation_id, "source_changed", None)
            )
            if checked.outcome != "needs_recovery":
                db.commit()
            else:
                try:
                    row = action_confirmations.execute(
                        db,
                        current.id,
                        company_id=current.company_id,
                        user_id=current.reviewer_user_id,
                        session_id=current.reviewer_session_id,
                    )
                except action_confirmations.ConfirmationError as exc:
                    db.rollback()
                    checked = ActionRecoveryObservation(observed.confirmation_id, exc.reason, None)
                else:
                    checked = ActionRecoveryObservation(row.id, row.status, row.final_audit_id)
            results.append(checked)
    cursor = observations[-1].confirmation_id if observations else through_id
    return ActionRecoveryBatch(
        cursor, len(observations) < limit or cursor == through_id, tuple(results)
    )


def decision_boundary(*, session_factory=SessionLocal):
    with session_factory() as db:
        return db.scalar(_scoped(select(func.max(DecisionRecord.id)), DecisionRecord)) or 0


def _observe_decision(db, row):
    if (
        row.status != DecisionStatus.executing.value
        or row.resolved_at is not None
        or row.superseded_by_id is not None
    ):
        return DecisionRecoveryObservation(row.id, "already_resolved", row.status)
    operation = db.execute(
        select(RuntimeOperation.id, RuntimeOperation.requested_action_count).where(
            RuntimeOperation.decision_id == row.id
        )
    ).first()
    if operation is None:
        return DecisionRecoveryObservation(row.id, "no_runtime_operation", row.status)
    received = db.scalar(
        select(func.count())
        .select_from(ToolAudit)
        .where(ToolAudit.runtime_operation_id == operation.id)
    )
    if received != operation.requested_action_count:
        return DecisionRecoveryObservation(row.id, "actions_incomplete", row.status)
    if any(
        audit.outcome == "confirmation_pending"
        for audit in decisions.effective_action_audits(db, row.id)
    ):
        return DecisionRecoveryObservation(row.id, "awaiting_confirmation", row.status)
    return DecisionRecoveryObservation(row.id, "needs_repair", row.status)


def reconcile_decision_receipts(*, through_id, after_id=0, limit=25, session_factory=SessionLocal):
    """Aggregate only a complete original set of effects; never replay an action."""
    _validate_page(through_id, after_id, limit)
    with session_factory() as db:
        rows = db.execute(
            _scoped(
                select(
                    DecisionRecord.id,
                    DecisionRecord.status,
                    DecisionRecord.resolved_at,
                    DecisionRecord.superseded_by_id,
                )
                .where(DecisionRecord.id > after_id, DecisionRecord.id <= through_id)
                .order_by(DecisionRecord.id)
                .limit(limit),
                DecisionRecord,
            )
        )
        observations = [_observe_decision(db, row) for row in rows]
    results = []
    for observed in observations:
        if observed.outcome != "needs_repair":
            results.append(observed)
            continue
        with session_factory() as db:
            begin_observation(db)
            row = db.scalar(
                _scoped(
                    select(DecisionRecord).where(DecisionRecord.id == observed.decision_id),
                    DecisionRecord,
                )
            )
            if row is None:
                checked = DecisionRecoveryObservation(observed.decision_id, "source_changed", None)
            else:
                checked = _observe_decision(db, row)
                if checked.outcome == "needs_repair":
                    decisions.refresh_from_receipts(db, row.id)
                    checked = DecisionRecoveryObservation(row.id, "repaired", row.status)
            db.commit()
            results.append(checked)
    cursor = observations[-1].decision_id if observations else through_id
    return DecisionRecoveryBatch(
        cursor, len(observations) < limit or cursor == through_id, tuple(results)
    )


def project_boundary(*, session_factory=SessionLocal):
    with session_factory() as db:
        return db.scalar(_scoped(select(func.max(Project.id)), Project)) or 0


def reconcile_projects(*, through_id, after_id=0, limit=25, session_factory=SessionLocal):
    _validate_page(through_id, after_id, limit)
    with session_factory() as db:
        rows = db.execute(
            _scoped(
                select(Project.id, Project.company_id, Project.status)
                .where(Project.id > after_id, Project.id <= through_id)
                .order_by(Project.id)
                .limit(limit),
                Project,
            )
        )
        observations = [project_completion.observe(db, row) for row in rows]
    results = []
    for observed in observations:
        if observed.outcome not in project_completion.REPAIRABLE:
            results.append(observed)
            continue
        with session_factory() as db:
            begin_observation(db)
            project = db.scalar(
                _scoped(select(Project).where(Project.id == observed.project_id), Project)
            )
            checked = (
                project_completion.CompletionObservation(
                    observed.project_id, "source_changed", None
                )
                if project is None
                else project_completion.reconcile(db, project)
            )
            db.commit()
            results.append(checked)
    cursor = observations[-1].project_id if observations else through_id
    return ProjectRecoveryBatch(
        cursor, len(observations) < limit or cursor == through_id, tuple(results)
    )

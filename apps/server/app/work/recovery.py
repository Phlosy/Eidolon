"""Bounded recovery of committed domain facts, without executing Runtime work."""

from dataclasses import dataclass

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.core.request_context import get_request_identity
from app.models.project import WorkSession
from app.models.runtime_artifact import RuntimeArtifactCollection
from app.models.runtime_execution import RuntimeAttempt
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


def _scoped(query):
    identity = get_request_identity()
    return query.where(WorkSession.company_id == identity.company_id) if identity else query


def work_session_boundary(*, session_factory=SessionLocal):
    """Capture a finite startup cohort; a later worker belongs to its live owner."""
    with session_factory() as db:
        return db.scalar(_scoped(select(func.max(WorkSession.id)))) or 0


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
    if (
        type(limit) is not int
        or not 1 <= limit <= MAX_BATCH
        or type(after_id) is not int
        or type(through_id) is not int
        or not 0 <= after_id <= through_id
    ):
        raise ValueError("invalid recovery page")
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

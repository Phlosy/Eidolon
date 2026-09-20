"""Original unresolved runs fence new sessions; bounded queries never submit work."""

import asyncio

from sqlalchemy import or_, select

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.request_context import get_request_identity
from app.models.project import WorkSession
from app.models.review import ReviewRequest
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.base import RuntimeProtocolError, RuntimeSession
from app.work.telemetry import AttemptRecorder, reconstruct_attempt

logger = get_logger(__name__)
MAX_QUERIES = 2
QUERY_TIMEOUT = 5


def unresolved_attempt_ids(db, *, company_id, employee_id, task_id=None, exclude_work_id=None):
    """Only canonical real execution facts; a failed host is not a remote end."""
    identity = get_request_identity()
    if identity is not None and identity.company_id != company_id:
        raise RuntimeProtocolError("execution_scope_unavailable")
    scopes = [WorkSession.employee_id == employee_id]
    if task_id is not None:
        scopes.extend(
            [
                WorkSession.task_id == task_id,
                WorkSession.review_request_id.in_(
                    select(ReviewRequest.id).where(
                        ReviewRequest.company_id == company_id, ReviewRequest.task_id == task_id
                    )
                ),
            ]
        )
    query = (
        select(RuntimeAttempt.id)
        .join(WorkSession, WorkSession.id == RuntimeAttempt.work_session_id)
        .where(
            WorkSession.company_id == company_id,
            WorkSession.runtime_type.in_(("hermes", "openclaw")),
            RuntimeAttempt.ended_at.is_(None),
            or_(*scopes),
        )
    )
    if exclude_work_id is not None:
        query = query.where(WorkSession.id != exclude_work_id)
    return list(db.scalars(query.order_by(RuntimeAttempt.id).limit(MAX_QUERIES)))


def unresolved_for_work(db, work):
    task_id = work.task_id if work.purpose == "worker" else None
    if work.purpose == "review":
        review = db.get(ReviewRequest, work.review_request_id)
        if review is not None and review.company_id == work.company_id:
            task_id = review.task_id
    return unresolved_attempt_ids(
        db,
        company_id=work.company_id,
        employee_id=work.employee_id,
        task_id=task_id,
        exclude_work_id=work.id,
    )


async def reconcile_attempts(attempt_ids, *, adapter_for, session_factory=SessionLocal):
    """Observe at most two saved runs. Callers must recheck admission after awaits."""
    for attempt_id in tuple(attempt_ids)[:MAX_QUERIES]:
        try:
            # Reconstruction validates original ownership, company and connection;
            # neither employee preferences nor a newly provisioned runtime select it.
            runtime_type, instance, reference = reconstruct_attempt(
                attempt_id, session_factory=session_factory
            )
            with session_factory() as db:
                row = db.get(RuntimeAttempt, attempt_id)
                if row.ended_at is not None:
                    continue
                work = db.get(WorkSession, row.work_session_id)
                recorder = AttemptRecorder(
                    row.id, row.request_key, row.idempotency_key, session_factory
                )
                session = RuntimeSession(
                    reference.local_session_ref,
                    instance,
                    work.task_id,
                    attempt=recorder,
                    remote_run_id=reference.remote_run_id,
                    remote_session_ref=reference.remote_session_ref,
                )
            adapter = adapter_for(runtime_type)
            if adapter.type != runtime_type:
                raise RuntimeProtocolError("execution_adapter_mismatch")
            async with asyncio.timeout(QUERY_TIMEOUT):
                result = await adapter.query_run(instance, reference)
            if result.status in {"completed", "failed"}:
                # Revalidate after the network wait and before admitting anything.
                recorder.terminal(result.status, result.source, session=session)
        except Exception:
            # Unknown remains unknown. Never persist remote text or guess a terminal.
            logger.warning("original runtime query unavailable", extra={"attempt_id": attempt_id})

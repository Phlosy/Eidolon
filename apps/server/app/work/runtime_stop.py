"""Stop a saved original attempt without starting or resubmitting any work."""

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.transaction_effects import after_commit
from app.models.project import WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.base import RuntimeProtocolError, RuntimeSession
from app.work.telemetry import AttemptRecorder, reconstruct_attempt, record_stop_intent


def request_task_stop(db, task_id, *, reason):
    """Task state and worker stop intent commit together; notification is only a nudge."""
    work_ids = list(
        db.scalars(
            select(WorkSession.id).where(
                WorkSession.task_id == task_id,
                WorkSession.purpose == "worker",
                WorkSession.status == "running",
            )
        )
    )
    if not work_ids:
        return
    for attempt_id in db.scalars(
        select(RuntimeAttempt.id).where(
            RuntimeAttempt.work_session_id.in_(work_ids),
        )
    ):
        record_stop_intent(db, attempt_id, reason)
    db.flush()

    def notify(identifier):
        from app.workflow.orchestrator import orchestrator

        orchestrator.notify({"type": "stop_work", "work_session_id": identifier})

    for work_id in work_ids:
        after_commit(db, lambda identifier=work_id: notify(identifier))


async def stop_attempt(attempt_id, *, adapter, reason, session_factory=SessionLocal):
    with session_factory() as db:
        row = db.get(RuntimeAttempt, attempt_id)
        if row is None:
            raise RuntimeProtocolError("execution_attempt_missing")
        recorder = AttemptRecorder(row.id, row.request_key, row.idempotency_key, session_factory)
        task_id = db.get(WorkSession, row.work_session_id).task_id
    status = recorder.request_stop(reason)
    if status in {"completed", "failed"}:
        return "terminal_observed"
    runtime_type, instance, reference = reconstruct_attempt(
        attempt_id, session_factory=session_factory
    )
    if adapter.type != runtime_type:
        raise RuntimeProtocolError("execution_adapter_mismatch")
    session = RuntimeSession(
        id=reference.local_session_ref,
        instance=instance,
        task_id=task_id,
        attempt=recorder,
        remote_run_id=reference.remote_run_id,
        remote_session_ref=reference.remote_session_ref,
        stop_reason=reason,
    )
    await adapter.cancel_task(session)
    with session_factory() as db:
        return db.get(RuntimeAttempt, attempt_id).stop_state

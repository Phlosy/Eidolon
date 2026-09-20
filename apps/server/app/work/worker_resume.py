"""Observe an orphan Worker's original run; never submit or invent execution.

The supported single-server owner calls this only for its captured startup cohort.
Native facts are queried outside transactions; the existing finalizer rechecks
domain/artifact eligibility and commits the result and host receipt together.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.request_context import get_request_identity
from app.models.project import Task, WorkSession
from app.models.runtime_execution import STOP_REASONS, RuntimeAttempt
from app.runtimes.base import RuntimeProtocolError, RuntimeRunReference, RuntimeSession
from app.runtimes.worker_recovery import POLL_INTERVAL, QUERY_TIMEOUT, recover_worker_wait
from app.work import runtime_artifacts, telemetry

logger = get_logger(__name__)
ARTIFACT_TIMEOUT = 30
_REASONS = {
    "execution_attempt_unknown",
    "execution_reference_unknown",
    "execution_deadline_unknown",
    "execution_deadline_expired",
    "execution_owner_changed",
    "execution_runtime_changed",
    "execution_connection_changed",
    "execution_connection_unavailable",
    "execution_stop_requested",
    "execution_retry_not_safe",
    "execution_adapter_mismatch",
    "recovery_wait_unavailable",
    "remote_failed",
}


async def _wait(adapter, session, row):
    if row.ended_at is not None:
        # Validate even an already saved terminal against this exact original
        # connection. It predates this process, so needs no fresh execution time.
        session.attempt.terminal(row.status, row.terminal_source, session=session)
        session.status = row.status
        return
    timeout = session.attempt.remaining_timeout(900)
    deadline = asyncio.get_running_loop().time() + timeout
    reference = RuntimeRunReference(session.id, session.remote_run_id, session.remote_session_ref)
    async with asyncio.timeout_at(deadline):
        while True:
            session.attempt.check_worker_wait(session=session)
            try:
                async with asyncio.timeout(QUERY_TIMEOUT):
                    observed = await adapter.query_run(session.instance, reference)
            except Exception as exc:
                if await recover_worker_wait(adapter, session, exc, deadline=deadline):
                    return
                raise RuntimeProtocolError("recovery_wait_unavailable") from None
            session.attempt.check_worker_wait(session=session)
            if observed.status in {"completed", "failed"}:
                session.attempt.terminal(observed.status, observed.source, session=session)
                session.status = observed.status
                return
            # Unknown is an observation, not a native failure or a new request.
            await asyncio.sleep(POLL_INTERVAL)


async def resume_worker(work_id, *, adapter_for, finalize, session_factory=SessionLocal):
    with session_factory() as db:
        work = db.get(WorkSession, work_id)
        identity = get_request_identity()
        if work is None or (identity is not None and identity.company_id != work.company_id):
            raise RuntimeProtocolError("execution_scope_unavailable")
        if (
            work.purpose != "worker"
            or work.runtime_type not in {"hermes", "openclaw"}
            or work.status != "running"
            or work.ended_at is not None
        ):
            return
        task_id = work.task_id
        row = db.scalar(
            select(RuntimeAttempt)
            .where(RuntimeAttempt.work_session_id == work.id, RuntimeAttempt.kind == "worker")
            .order_by(RuntimeAttempt.id.desc())
            .limit(1)
        )
        stop_reason = db.scalar(
            select(RuntimeAttempt.stop_reason)
            .where(
                RuntimeAttempt.work_session_id == work.id,
                RuntimeAttempt.stop_requested_at.is_not(None),
            )
            .order_by(RuntimeAttempt.id)
            .limit(1)
        )
        task = db.get(Task, task_id)
        if stop_reason is None and (task is None or task.status != "in_progress"):
            stop_reason = (
                "blocked" if task is not None and task.status == "blocked" else "cancelled"
            )

    adapter = session = None
    error = "recovery_unavailable"
    stop = stop_reason or "host_error"
    cancelled = False
    try:
        if row is None:
            raise RuntimeProtocolError("execution_attempt_unknown")
        kind, instance, reference = telemetry.reconstruct_attempt(
            row.id, session_factory=session_factory
        )
        session = RuntimeSession(
            reference.local_session_ref,
            instance,
            task_id,
            attempt=telemetry.AttemptRecorder(
                row.id, row.request_key, row.idempotency_key, session_factory
            ),
            remote_run_id=reference.remote_run_id,
            remote_session_ref=reference.remote_session_ref,
        )
        adapter = adapter_for(kind)
        if adapter.type != kind:
            adapter = None
            raise RuntimeProtocolError("execution_adapter_mismatch")
        if stop_reason is not None or row.host_outcome in {"failed", "cancelled"}:
            raise RuntimeProtocolError("execution_stop_requested")
        try:
            await _wait(adapter, session, row)
        except TimeoutError:
            stop = "timeout"
            raise RuntimeProtocolError("execution_deadline_expired") from None
        if session.status != "completed":
            raise RuntimeProtocolError("remote_failed")
        runtime_artifacts.bind_context(row.artifact_context_json, session)
        async with asyncio.timeout(ARTIFACT_TIMEOUT):
            produced = await adapter.get_artifacts(session)
        # The finalizer atomically rechecks stop/task/identity/artifact facts.
        finalize(
            task_id,
            True,
            produced,
            None,
            None,
            work_session_id=work_id,
            runtime_session=session,
            recovery_attempt_id=row.id,
        )
        return
    except SQLAlchemyError:
        # Storage failure must retry the same source, never reinterpret it as a
        # remote failure and commit a contradictory domain outcome.
        raise
    except asyncio.CancelledError as exc:
        cancelled = True
        stop = exc.args[0] if exc.args and exc.args[0] in STOP_REASONS else "cancelled"
        error = stop if stop in {"budget_denied", "host_error"} else "cancelled"
    except Exception as exc:
        if isinstance(exc, RuntimeProtocolError) and str(exc) in _REASONS:
            error = str(exc)
        else:
            logger.warning(
                "original worker recovery unavailable", extra={"work_session_id": work_id}
            )

    if adapter is not None and session is not None:
        session.stop_reason = stop
        try:
            # Native cancel owns a bounded, shielded cleanup and exact identity
            # check, including under repeated cancellation of this coroutine.
            await adapter.cancel_task(session)
        except asyncio.CancelledError:
            if not cancelled:
                error = "cancelled"
            cancelled = True
        except SQLAlchemyError:
            raise
        except Exception:
            logger.warning("recovered worker stop unconfirmed", extra={"work_session_id": work_id})
    finalize(
        task_id,
        False,
        [],
        error,
        None,
        work_session_id=work_id,
        recovery_attempt_id=row.id if row is not None else None,
    )
    if cancelled:
        raise asyncio.CancelledError

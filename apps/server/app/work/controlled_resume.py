"""Recover an original controlled request without opening another model turn."""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.core.request_context import get_request_identity
from app.core.transaction_effects import ensure_sqlite_transaction
from app.models.decision import ToolAudit
from app.models.management_dispatch import ManagementDispatch
from app.models.project import WorkSession
from app.models.runtime_execution import STOP_REASONS, RuntimeAttempt
from app.models.runtime_operation import RuntimeOperation
from app.runtimes.base import (
    RuntimeProtocolError,
    RuntimeRunReference,
    RuntimeSession,
    RuntimeStatus,
)
from app.work import decisions, telemetry
from app.work import runtime_bridge as bridge
from app.work.runtime_conversation import _binding
from app.work.tool_executor import ToolExecutionError

QUERY_TIMEOUT = 5
POLL_INTERVAL = 0.5
_REASONS = {
    "execution_attempt_unknown",
    "execution_reference_unknown",
    "execution_deadline_unknown",
    "execution_deadline_expired",
    "execution_owner_changed",
    "execution_runtime_changed",
    "execution_identity_unknown",
    "execution_connection_changed",
    "execution_connection_unavailable",
    "execution_adapter_mismatch",
    "execution_source_changed",
    "execution_session_ended",
    "request_source_unknown",
    "remote_failed",
    "recovery_wait_unavailable",
}


@dataclass(frozen=True)
class OriginalConversation:
    work_session_id: int
    company_id: int | None
    recipient_employee_id: int
    purpose: str
    project_id: int | None
    review_request_id: int | None
    runtime_type: str
    runtime_instance_id: int | None
    runtime_session_ref: str | None
    attempt_id: int | None
    source_event_id: int | None


def source_for_work(work_id, *, session_factory=SessionLocal):
    with session_factory() as db:
        work = db.get(WorkSession, work_id)
        identity = get_request_identity()
        if work is None or (identity is not None and identity.company_id != work.company_id):
            return None
        if (
            work.purpose not in {"management", "review"}
            or work.task_id is not None
            or work.runtime_type not in {"hermes", "openclaw"}
            or work.status != "running"
            or work.ended_at is not None
        ):
            return None
        attempt_id = db.scalar(
            select(func.max(RuntimeAttempt.id)).where(RuntimeAttempt.work_session_id == work.id)
        )
        source_event_id = db.scalar(
            select(ManagementDispatch.source_event_id).where(
                ManagementDispatch.work_session_id == work.id,
                ManagementDispatch.company_id == work.company_id,
            )
        )
        return OriginalConversation(
            work.id,
            work.company_id,
            work.employee_id,
            work.purpose,
            work.project_id,
            work.review_request_id,
            work.runtime_type,
            work.runtime_instance_id,
            work.runtime_session_ref,
            attempt_id,
            source_event_id,
        )


def matching_work(db, source):
    """Match the captured source, including an originally absent attempt."""
    work = db.get(WorkSession, source.work_session_id, populate_existing=True)
    identity = get_request_identity()
    if work is None or (identity is not None and identity.company_id != work.company_id):
        return None
    if (
        work.company_id != source.company_id
        or work.employee_id != source.recipient_employee_id
        or work.purpose != source.purpose
        or work.project_id != source.project_id
        or work.review_request_id != source.review_request_id
        or work.runtime_type != source.runtime_type
        or work.runtime_instance_id != source.runtime_instance_id
        or work.runtime_session_ref != source.runtime_session_ref
        or work.task_id is not None
        or db.scalar(
            select(func.max(RuntimeAttempt.id)).where(RuntimeAttempt.work_session_id == work.id)
        )
        != source.attempt_id
    ):
        return None
    return work


def current_source(db, source):
    work = matching_work(db, source)
    if work is None:
        raise RuntimeProtocolError("execution_source_changed")
    if work.status != "running" or work.ended_at is not None:
        raise RuntimeProtocolError("execution_session_ended")
    row = (
        db.get(RuntimeAttempt, source.attempt_id, populate_existing=True)
        if source.attempt_id
        else None
    )
    if row is None:
        raise RuntimeProtocolError("execution_attempt_unknown")
    if row.kind != "controlled":
        raise RuntimeProtocolError("execution_source_changed")
    return row


def stop_reason(db, source):
    return db.scalar(
        select(RuntimeAttempt.stop_reason)
        .where(
            RuntimeAttempt.work_session_id == source.work_session_id,
            RuntimeAttempt.stop_requested_at.is_not(None),
        )
        .order_by(RuntimeAttempt.id)
        .limit(1)
    )


def _check_stop(db, source):
    stop = stop_reason(db, source)
    if stop is not None:
        raise bridge.BridgeError(stop)


def stopped_outcome(reason):
    return {
        "status": "cancelled"
        if reason in {"cancelled", "reassigned", "blocked", "shutdown", "budget_denied"}
        else "failed",
        "reason": reason,
    }


def _received(source, session_factory):
    with session_factory() as db:
        ensure_sqlite_transaction(db)
        row = current_source(db, source)
        _check_stop(db, source)
        if row.ended_at is None:
            return None, None
        if row.status != "completed":
            raise RuntimeProtocolError("remote_failed")
        if row.reply_operation_id is None:
            raise RuntimeProtocolError("request_source_unknown")
        bridge._reply_attempt(db, source.work_session_id, row.id)
        operation = db.get(RuntimeOperation, row.reply_operation_id)
        if operation is None or operation.work_session_id != source.work_session_id:
            raise RuntimeProtocolError("execution_source_changed")
        body = bridge.stored_request_body(operation)
        request = bridge.parse_reply(body)
        if request["kind"] == "finish":
            return {"status": "finished", "reason": "finish", "summary": request["summary"]}, None
        count = db.scalar(
            select(func.count())
            .select_from(ToolAudit)
            .where(ToolAudit.runtime_operation_id == operation.id)
        )
        if count == operation.requested_action_count:
            if operation.decision_id is not None:
                decisions.refresh_from_receipts(db, operation.decision_id)
                db.commit()
            return {"status": "stopped", "reason": "conversation_interrupted"}, None
        return None, body


def _reconstruct(source, session_factory):
    kind, instance, reference = telemetry.reconstruct_attempt(
        source.attempt_id, session_factory=session_factory
    )
    with session_factory() as db:
        work = matching_work(db, source)
        if work is None:
            raise RuntimeProtocolError("execution_source_changed")
        row = db.get(RuntimeAttempt, source.attempt_id)
        runtime = telemetry.validate_identity(db, work, fingerprint=row.instance_fingerprint)
        instance.status = (
            RuntimeStatus.running
            if runtime.status in {"running", "idle"}
            else RuntimeStatus.stopped
        )
        instance.details.update(
            container_name=runtime.container_name,
            runtime_version=runtime.runtime_version,
            resource_key=runtime.resource_key,
        )
        session = RuntimeSession(
            reference.local_session_ref,
            instance,
            None,
            attempt=telemetry.AttemptRecorder(
                row.id, row.request_key, row.idempotency_key, session_factory
            ),
            remote_run_id=reference.remote_run_id,
            remote_session_ref=reference.remote_session_ref,
        )
        session.conversation_started = True
        # This is the still-running host conversation. Its latest native turn
        # may already be terminal while original accepted actions remain.
    return kind, session


async def _cancel_original(source, session, adapter_for, reason, session_factory):
    # Save withdrawal even when the saved handle cannot safely be reconstructed.
    # A concurrently ended host still owns this original remote cleanup obligation.
    with session_factory() as db:
        ensure_sqlite_transaction(db)
        if matching_work(db, source) is None or source.attempt_id is None:
            return session
        row = db.get(RuntimeAttempt, source.attempt_id)
        if row.ended_at is not None or row.kind != "controlled":
            return session
        telemetry.record_stop_intent(db, row.id, reason)
        db.commit()
    try:
        kind, original = _reconstruct(source, session_factory)
        adapter = adapter_for(kind)
        if adapter.type != kind:
            return session
    except RuntimeProtocolError:
        return session
    original.stop_reason = reason
    try:
        await adapter.cancel_task(original)
    except SQLAlchemyError:
        raise
    except Exception:
        original.cancel_confirmed = False
    return original


async def resume_conversation(source, *, adapter_for, finalize, session_factory=SessionLocal):
    """The runner owns cleanup and retries; no native submission occurs here."""
    session = adapter = None
    cancelled = False
    stop = "host_error"
    outcome = {"status": "failed", "reason": "recovery_unavailable"}
    try:
        outcome, body = _received(source, session_factory)
        if outcome is None:
            kind, session = _reconstruct(source, session_factory)
            adapter = adapter_for(kind)
            if adapter.type != kind:
                adapter = None
                raise RuntimeProtocolError("execution_adapter_mismatch")
            remaining = session.attempt.remaining_timeout(900)
            deadline = asyncio.get_running_loop().time() + remaining

            @contextmanager
            def checked_session():
                with session_factory() as db:
                    ensure_sqlite_transaction(db)
                    if asyncio.current_task().cancelling():
                        raise asyncio.CancelledError
                    if asyncio.get_running_loop().time() >= deadline:
                        raise RuntimeProtocolError("execution_deadline_expired")
                    current_source(db, source)
                    _check_stop(db, source)
                    _binding(db, source.work_session_id, adapter, session)
                    yield db

            if body is None:
                reference = RuntimeRunReference(
                    session.id, session.remote_run_id, session.remote_session_ref
                )
                failures = 0
                async with asyncio.timeout_at(deadline):
                    while True:
                        with checked_session():
                            pass
                        try:
                            async with asyncio.timeout(QUERY_TIMEOUT):
                                observed = await adapter.query_run(session.instance, reference)
                        except Exception:
                            failures += 1
                            if failures >= 2:
                                raise RuntimeProtocolError("recovery_wait_unavailable") from None
                            await asyncio.sleep(POLL_INTERVAL)
                            continue
                        with checked_session():
                            pass
                        if observed.status in {"completed", "failed"}:
                            session.attempt.terminal(
                                observed.status, observed.source, session=session
                            )
                            break
                        await asyncio.sleep(POLL_INTERVAL)
                outcome, body = _received(source, session_factory)
            if body is not None:
                receipt = bridge.handle_reply(
                    source.work_session_id,
                    body,
                    session_factory=checked_session,
                    _runtime_attempt_id=source.attempt_id,
                )
                outcome = {
                    "status": "stopped",
                    "reason": receipt.get("reason")
                    if receipt["status"] == "interrupted"
                    else "conversation_interrupted",
                }
    except SQLAlchemyError:
        raise
    except asyncio.CancelledError as exc:
        cancelled = True
        stop = exc.args[0] if exc.args and exc.args[0] in STOP_REASONS else "cancelled"
        outcome = {"status": "cancelled", "reason": stop}
    except TimeoutError:
        stop = "timeout"
        outcome = {"status": "failed", "reason": "execution_deadline_expired"}
    except Exception as exc:
        if isinstance(exc, bridge.BridgeError) and exc.reason == "storage_failed":
            if isinstance(exc.__cause__, SQLAlchemyError):
                raise exc.__cause__ from None
            raise
        reason = "recovery_unavailable"
        if isinstance(exc, (bridge.BridgeError, ToolExecutionError)):
            reason = exc.reason
        elif isinstance(exc, RuntimeProtocolError) and str(exc) in _REASONS:
            reason = str(exc)
        stop = "host_error"
        if reason in STOP_REASONS:
            stop = reason
        elif reason == "execution_deadline_expired":
            stop = "timeout"
        outcome = stopped_outcome(reason)

    outcome["stop_reason"] = stop

    async def finish():
        original = await _cancel_original(source, session, adapter_for, stop, session_factory)
        await finalize(source, outcome, original)

    cleanup = asyncio.create_task(
        finish(), name=f"controlled-recovery-finalize-{source.work_session_id}"
    )
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError

"""Single writer for U13 attempts/observations. No Task, Review or Decision writes.

Attempt identity/outcomes are retained. Structured telemetry expires after seven
days; token fragments, prompts, tool arguments, error bodies and credentials are
never persisted here. Unknown remote outcomes remain open for reconciliation.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.request_context import get_request_identity
from app.core.transaction_effects import ensure_sqlite_transaction
from app.models.base import as_utc, utcnow
from app.models.organization import Employee
from app.models.project import WorkSession
from app.models.runtime import RuntimeInstance as RuntimeRow
from app.models.runtime_execution import RuntimeAttempt, RuntimeTelemetry
from app.providers.secrets.store import get_secret_store
from app.repositories import persons
from app.runtimes.base import (
    RuntimeEvent,
    RuntimeInstance,
    RuntimeProtocolError,
    RuntimeRunReference,
)
from app.runtimes.probe import instance_fingerprint

RETENTION_DAYS = 7
MAX_PAGE_SIZE = 100
logger = get_logger(__name__)
_TERMINAL_SOURCES = {"hermes.status", "hermes.sse", "openclaw.wait", "host.reply", "mock.stream"}
_HOST_REASONS = {
    "reply_received",
    "stream_completed",
    "stream_ended",
    "runtime_error",
    "host_error",
    "cancelled",
}


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _identity(db, work, session=None, *, fingerprint=None):
    if work is None or not isinstance(work.provenance_snapshot_json, dict):
        raise RuntimeProtocolError("execution_identity_unknown")
    identity = get_request_identity()
    actor = db.get(Employee, work.employee_id)
    if (
        (identity is not None and identity.company_id != work.company_id)
        or actor is None
        or actor.company_id != work.company_id
        or persons.resolve_person_id(db, work.employee_id) != work.person_id
    ):
        raise RuntimeProtocolError("execution_owner_changed")
    if session is not None and (
        session.id != work.runtime_session_ref
        or session.task_id != work.task_id
        or session.instance.employee_id != work.employee_id
    ):
        raise RuntimeProtocolError("execution_identity_mismatch")
    if work.runtime_type == "mock":
        if work.runtime_instance_id is not None:
            raise RuntimeProtocolError("execution_identity_mismatch")
        return None
    row = db.get(RuntimeRow, work.runtime_instance_id)
    original = work.provenance_snapshot_json.get("runtime") or {}
    if (
        row is None
        or not persons.matches_owner(db, work.employee_id, row.person_id, row.employee_id)
        or row.person_id != work.person_id
        or row.employee_id not in {None, work.employee_id}
        or any(
            (
                row.runtime_type != work.runtime_type,
                row.deployment_mode != work.deployment_mode,
                original.get("instance_id") != row.id,
                original.get("resource_key") != row.resource_key,
                original.get("version") != row.runtime_version,
                original.get("image_digest") != row.image_digest,
            )
        )
    ):
        raise RuntimeProtocolError("execution_runtime_changed")
    current = instance_fingerprint(row)
    if fingerprint is not None and current != fingerprint:
        raise RuntimeProtocolError("execution_runtime_changed")
    if session is not None:
        details = session.instance.details
        if details.get("row_id") != row.id or details.get("instance_fingerprint") != current:
            raise RuntimeProtocolError("execution_runtime_changed")
        metadata = row.metadata_json or {}
        if (
            details.get("base_url") != f"http://{row.internal_host}:{row.internal_port}"
            or details.get("api_key")
            != get_secret_store().retrieve(db, metadata.get("api_key_ref"))
            or details.get("device_key")
            != get_secret_store().retrieve(db, metadata.get("device_key_ref"))
        ):
            raise RuntimeProtocolError("execution_connection_changed")
    return row


def prepare_attempt(
    work_session_id,
    session,
    prompt,
    *,
    request_key,
    kind,
    retry_of=None,
    request_context=None,
    session_factory=SessionLocal,
):
    """Commit intent before send; retry is explicit, same-request and never dialogue.

    request_digest covers canonical host inputs (prompt, declared rendering
    context, stable session and runtime identity), not transport framing or a
    transient timeout. Only the digest is saved, never these input contents.
    """
    if (
        kind not in {"worker", "controlled"}
        or not isinstance(request_key, str)
        or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_key)
    ):
        raise RuntimeProtocolError("invalid_execution_request")
    if not isinstance(prompt, str) or len(prompt.encode("utf-8")) > 1024 * 1024:
        raise RuntimeProtocolError("invalid_execution_request")
    with session_factory() as db:
        ensure_sqlite_transaction(db)
        work = db.get(WorkSession, work_session_id)
        row = _identity(db, work, session)
        digest = _digest(
            json.dumps(
                {
                    "prompt": prompt,
                    "context": request_context,
                    "session": session.id,
                    "request": request_key,
                    "kind": kind,
                    "runtime": instance_fingerprint(row) if row else "mock",
                },
                sort_keys=True,
                allow_nan=False,
            )
        )
        if (
            work.status != "running"
            or work.ended_at is not None
            or (kind == "worker" and work.purpose != "worker")
        ):
            raise RuntimeProtocolError("execution_session_ended")
        previous = db.scalar(
            select(RuntimeAttempt)
            .where(
                RuntimeAttempt.work_session_id == work.id, RuntimeAttempt.request_key == request_key
            )
            .order_by(RuntimeAttempt.attempt_number.desc())
            .limit(1)
        )
        if previous is not None and (previous.request_digest != digest or previous.kind != kind):
            raise RuntimeProtocolError("execution_request_conflict")
        if retry_of is not None:
            # An uncertain remote run may still be doing work. U15 owns a safe
            # cancellation/retry policy; this writer never invents one.
            if previous is None or previous.id != retry_of or previous.status != "failed":
                raise RuntimeProtocolError("execution_retry_not_safe")
            if work.technical_retry_count is not None:
                work.technical_retry_count += 1
        elif previous is not None:
            recorder = AttemptRecorder(
                previous.id, previous.request_key, previous.idempotency_key, session_factory
            )
            session.attempt = recorder
            return recorder
        number = previous.attempt_number + 1 if previous else 1
        key = (
            (session.id if kind == "worker" else _digest(f"{session.id}\0{request_key}"))
            if number == 1
            else _digest(f"{session.id}\0{request_key}\0{number}")
        )
        remote_session = (
            (f"agent:main:eidolon-{session.id}" if kind == "controlled" else "agent:main:main")
            if work.runtime_type == "openclaw"
            else session.id
        )
        now = utcnow()
        attempt = RuntimeAttempt(
            work_session_id=work.id,
            request_key=request_key,
            attempt_number=number,
            idempotency_key=key,
            request_digest=digest,
            local_session_ref=session.id,
            runtime_instance_id=work.runtime_instance_id,
            instance_fingerprint=instance_fingerprint(row) if row else None,
            kind=kind,
            remote_session_ref=remote_session,
            status="prepared",
            last_progress_at=now,
        )
        db.add(attempt)
        db.flush()
        recorder = AttemptRecorder(attempt.id, request_key, key, session_factory)
        db.commit()
    session.attempt = recorder
    return recorder


def _attempt(db, identifier):
    row = db.get(RuntimeAttempt, identifier)
    if row is None:
        raise RuntimeProtocolError("execution_attempt_missing")
    return row


@dataclass(frozen=True)
class AttemptRecorder:
    attempt_id: int
    request_key: str
    idempotency_key: str
    session_factory: object = SessionLocal

    def accepted(self, run_id, remote_session_ref):
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", run_id):
            raise RuntimeProtocolError("run_id_invalid")
        with self.session_factory() as db:
            ensure_sqlite_transaction(db)
            row = _attempt(db, self.attempt_id)
            if row.remote_session_ref != remote_session_ref or (
                row.remote_run_id is not None and row.remote_run_id != run_id
            ):
                raise RuntimeProtocolError("execution_acceptance_conflict")
            if row.remote_run_id is None:
                if row.ended_at is not None:
                    raise RuntimeProtocolError("execution_already_terminal")
                row.remote_run_id, row.accepted_at = run_id, utcnow()
                row.status, row.last_progress_at = "accepted", row.accepted_at
                db.flush()
                _record(db, row, "session.started", {}, "accepted", row.accepted_at)
            db.commit()

    def terminal(self, status, source):
        if status not in {"completed", "failed"} or source not in _TERMINAL_SOURCES:
            raise RuntimeProtocolError("invalid_remote_outcome")
        with self.session_factory() as db:
            ensure_sqlite_transaction(db)
            row = _attempt(db, self.attempt_id)
            if row.ended_at is not None:
                if row.status != status:
                    raise RuntimeProtocolError("execution_terminal_conflict")
                return
            if row.remote_run_id is None and source != "mock.stream":
                raise RuntimeProtocolError("execution_acceptance_missing")
            if (
                source == "mock.stream"
                and db.get(WorkSession, row.work_session_id).runtime_type != "mock"
            ):
                raise RuntimeProtocolError("execution_terminal_source_mismatch")
            row.status, row.terminal_source, row.ended_at = status, source, utcnow()
            row.last_progress_at = row.ended_at
            db.flush()
            _record(db, row, f"session.{status}", {}, f"terminal:{status}", row.ended_at)
            db.commit()

    def finish_host(self, outcome, reason):
        if outcome not in {"completed", "failed", "cancelled"} or reason not in _HOST_REASONS:
            raise RuntimeProtocolError("invalid_host_outcome")
        with self.session_factory() as db:
            ensure_sqlite_transaction(db)
            row = _attempt(db, self.attempt_id)
            if row.host_outcome is not None:
                return
            row.host_outcome, row.host_reason, row.host_observed_at = outcome, reason, utcnow()
            if row.ended_at is None:
                row.status = "unknown"
            db.commit()

    def finish_host_safely(self, outcome, reason):
        """Preserve primary cancellation/failure and disclose an unsaved observation."""
        try:
            self.finish_host(outcome, reason)
            return True
        except SQLAlchemyError:
            # Database exceptions can contain SQL parameters or transport text.
            # A diagnostic storage failure must not skip cleanup or imply a receipt.
            logger.warning(
                "runtime host outcome could not be saved", extra={"attempt_id": self.attempt_id}
            )
            return False

    def observe(self, event):
        mapped = _map(event)
        if mapped is None:
            return
        kind, details = mapped
        occurred = (
            as_utc(event.ts)
            if isinstance(event, RuntimeEvent) and isinstance(event.ts, datetime)
            else None
        )
        now = utcnow()
        if occurred is None or occurred > now + timedelta(minutes=5):
            occurred = now
            kind, details = "runtime.warning", {"reason": "invalid_timestamp"}
        # Never keep provider-supplied identifiers or payloads as diagnostic text.
        data = (
            event.data if isinstance(event, RuntimeEvent) and isinstance(event.data, dict) else {}
        )
        external_key = data.get("event_id")
        event_key = (
            ("external:" + external_key)
            if isinstance(external_key, str) and len(external_key) <= 256
            else json.dumps([kind, details, occurred.isoformat()], sort_keys=True)
        )
        with self.session_factory() as db:
            ensure_sqlite_transaction(db)
            row = _attempt(db, self.attempt_id)
            _record(db, row, kind, details, event_key, occurred)
            db.commit()


def _usage(value):
    if not isinstance(value, dict):
        return None
    keys = ("input_tokens", "output_tokens")
    if not all(type(value.get(k)) is int and 0 <= value[k] < 2**63 for k in keys):
        return None
    return {key: value[key] for key in keys}


def _map(event):
    if (
        not isinstance(event, RuntimeEvent)
        or not isinstance(event.kind, str)
        or not isinstance(event.data, dict)
    ):
        return "runtime.warning", {"reason": "invalid_event"}
    kind, data = event.kind, event.data
    if kind == "thinking" or (kind == "message" and data.get("complete") is not True):
        return None
    mapped = {
        "completed": "session.completed",
        "error": "runtime.error",
        "artifact": "artifact.created",
        "message": "message.completed",
    }.get(kind)
    if kind == "tool_call":
        stage = data.get("event")
        mapped = (
            stage
            if isinstance(stage, str) and stage in {"tool.completed", "tool.failed"}
            else "tool.called"
        )
    if mapped is None:
        return "runtime.warning", {"reason": "unrecognized_event"}
    details = {}
    if "usage" in data:
        usage = _usage(data["usage"])
        details = {"usage": usage} if usage is not None else {"reason": "usage_unknown"}
    return mapped, details


def _record(db, attempt, kind, details, event_key, occurred):
    prune_telemetry(db, limit=100)
    key = _digest(event_key)
    if (
        db.scalar(
            select(RuntimeTelemetry.id).where(
                RuntimeTelemetry.attempt_id == attempt.id, RuntimeTelemetry.event_key == key
            )
        )
        is not None
    ):
        return
    now = utcnow()
    db.add(
        RuntimeTelemetry(
            work_session_id=attempt.work_session_id,
            attempt_id=attempt.id,
            event_key=key,
            kind=kind,
            details_json=details,
            occurred_at=occurred,
            received_at=now,
            expires_at=now + timedelta(days=RETENTION_DAYS),
        )
    )
    # Late telemetry never changes a terminal record or any business state.
    if attempt.ended_at is None:
        attempt.last_progress_at = max(as_utc(attempt.last_progress_at), now)


def list_telemetry(db, work_session_id, *, after_id=0, limit=50, now=None):
    if (
        type(limit) is not int
        or not 1 <= limit <= MAX_PAGE_SIZE
        or type(after_id) is not int
        or after_id < 0
    ):
        raise ValueError("invalid telemetry page")
    _read_scope(db, work_session_id)
    return list(
        db.scalars(
            select(RuntimeTelemetry)
            .where(
                RuntimeTelemetry.work_session_id == work_session_id,
                RuntimeTelemetry.id > after_id,
                RuntimeTelemetry.expires_at > (now or utcnow()),
            )
            .order_by(RuntimeTelemetry.id)
            .limit(limit)
        )
    )


def _read_scope(db, work_session_id):
    work = db.get(WorkSession, work_session_id)
    identity = get_request_identity()
    if work is None or (identity is not None and work.company_id != identity.company_id):
        raise RuntimeProtocolError("execution_scope_unavailable")


def list_attempts(db, work_session_id, *, after_id=0, limit=50):
    if (
        type(limit) is not int
        or not 1 <= limit <= MAX_PAGE_SIZE
        or type(after_id) is not int
        or after_id < 0
    ):
        raise ValueError("invalid attempt page")
    _read_scope(db, work_session_id)
    return list(
        db.scalars(
            select(RuntimeAttempt)
            .where(RuntimeAttempt.work_session_id == work_session_id, RuntimeAttempt.id > after_id)
            .order_by(RuntimeAttempt.id)
            .limit(limit)
        )
    )


def prune_telemetry(db, *, limit=500, now=None):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("invalid retention batch")
    ids = list(
        db.scalars(
            select(RuntimeTelemetry.id)
            .where(RuntimeTelemetry.expires_at <= (now or utcnow()))
            .order_by(RuntimeTelemetry.expires_at, RuntimeTelemetry.id)
            .limit(limit)
        )
    )
    if ids:
        db.execute(delete(RuntimeTelemetry).where(RuntimeTelemetry.id.in_(ids)))
    return len(ids)


def reconstruct_attempt(attempt_id, *, session_factory=SessionLocal):
    """Read a saved run using its original runtime row; never resolve by new binding."""
    with session_factory() as db:
        attempt = _attempt(db, attempt_id)
        work = db.get(WorkSession, attempt.work_session_id)
        row = _identity(db, work, fingerprint=attempt.instance_fingerprint)
        if (
            row is None
            or row.id != attempt.runtime_instance_id
            or work.runtime_session_ref != attempt.local_session_ref
        ):
            raise RuntimeProtocolError("execution_runtime_changed")
        if not attempt.remote_run_id:
            raise RuntimeProtocolError("execution_reference_unknown")
        meta = row.metadata_json or {}
        token = get_secret_store().retrieve(db, meta.get("api_key_ref"))
        device = get_secret_store().retrieve(db, meta.get("device_key_ref"))
        if not row.internal_host or not row.internal_port or not token:
            raise RuntimeProtocolError("execution_connection_unavailable")
        instance = RuntimeInstance(
            employee_id=work.employee_id,
            profile=row.container_name or "saved-run",
            home_path=row.workspace_path,
            details={
                "base_url": f"http://{row.internal_host}:{row.internal_port}",
                "api_key": token,
                "device_key": device,
            },
        )
        return (
            work.runtime_type,
            instance,
            RuntimeRunReference(
                attempt.local_session_ref, attempt.remote_run_id, attempt.remote_session_ref
            ),
        )


async def query_attempt(attempt_id, *, adapter, session_factory=SessionLocal):
    runtime_type, instance, reference = reconstruct_attempt(
        attempt_id, session_factory=session_factory
    )
    if adapter.type != runtime_type:
        raise RuntimeProtocolError("execution_adapter_mismatch")
    return await adapter.query_run(instance, reference)

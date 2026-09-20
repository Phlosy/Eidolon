"""Read a spending permit without repeating admission/reservation side effects.

The caller's provider/relay owns cumulative amounts and the actual spending cap.
This module only turns a withdrawn permit into durable original-session stops.
"""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.request_context import get_request_identity
from app.core.transaction_effects import ensure_sqlite_transaction
from app.models.project import WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.base import RuntimeProtocolError
from app.work.telemetry import record_stop_intent

BudgetMonitor = Callable[[int], Awaitable[bool]]
READ_TIMEOUT_SECONDS = 2
logger = get_logger(__name__)


def _snapshot(work):
    if (
        work is None
        or work.runtime_type not in {"hermes", "openclaw"}
        or work.status != "running"
        or work.ended_at is not None
        or not isinstance(work.provenance_snapshot_json, dict)
    ):
        return None
    identity = get_request_identity()
    if identity is not None and identity.company_id != work.company_id:
        raise RuntimeProtocolError("execution_budget_scope")
    return (
        work.id,
        work.created_at,
        work.company_id,
        work.person_id,
        deepcopy(work.provenance_snapshot_json),
    )


async def stop_reason(work_id, monitor, *, is_current, session_factory=SessionLocal):
    """No database transaction spans the bounded, read-only external check."""
    if monitor is None or not is_current():
        return None
    with session_factory() as db:
        original = _snapshot(db.get(WorkSession, work_id))
    if original is None:
        return None
    allowed = None
    try:
        async with asyncio.timeout(READ_TIMEOUT_SECONDS) as timeout:
            allowed = await monitor(work_id)
        if timeout.expired():
            allowed = None
    except Exception:
        logger.warning(
            "execution budget permission unavailable", extra={"work_session_id": work_id}
        )
    if allowed is True or not is_current():
        return None
    reason = "budget_denied" if allowed is False else "host_error"
    try:
        with session_factory() as db:
            ensure_sqlite_transaction(db)
            if _snapshot(db.get(WorkSession, work_id)) != original or not is_current():
                return None
            first_reason = db.scalar(
                select(RuntimeAttempt.stop_reason)
                .where(
                    RuntimeAttempt.work_session_id == work_id,
                    RuntimeAttempt.stop_requested_at.is_not(None),
                )
                .order_by(RuntimeAttempt.stop_requested_at, RuntimeAttempt.id)
                .limit(1)
            )
            reason = first_reason or reason
            attempts = db.scalars(
                select(RuntimeAttempt).where(
                    RuntimeAttempt.work_session_id == work_id, RuntimeAttempt.ended_at.is_(None)
                )
            )
            for attempt in attempts:
                record_stop_intent(db, attempt.id, reason)
            db.commit()
    except Exception:
        # The owned adapter cleanup retries intent persistence before remote I/O.
        # A storage outage must not make the local owner continue on a denied permit.
        logger.warning(
            "execution budget stop could not be saved", extra={"work_session_id": work_id}
        )
    return reason if is_current() else None

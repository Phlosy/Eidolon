"""Finite project allowance for durable host requests, not Provider spending.

The immutable RuntimeAttempt history is the counter. Unknown, failed and stopped
requests still count; a new WorkSession or process never replenishes the allowance.
Native model calls inside a Runtime request are outside this counter.
"""

from sqlalchemy import func, select

from app.core.config import settings
from app.core.request_context import get_request_identity
from app.core.transaction_effects import ensure_sqlite_transaction
from app.models.project import Project, WorkSession
from app.models.runtime_execution import RuntimeAttempt
from app.runtimes.base import RuntimeProtocolError

EXHAUSTED = "execution_request_budget_exhausted"
UNAVAILABLE = "execution_request_budget_unavailable"
DENIED_REASONS = {EXHAUSTED, UNAVAILABLE}


def project_allowance(db, *, company_id, project_id, lock=False):
    identity = get_request_identity()
    if identity is not None and identity.company_id != company_id:
        raise RuntimeProtocolError("execution_request_budget_scope")
    query = select(Project.id).where(Project.id == project_id, Project.company_id == company_id)
    if lock:
        # SQLite takes its physical write lock before any allowance reads. The
        # canonical project row serializes request admission on row-locking DBs.
        ensure_sqlite_transaction(db)
        query = query.with_for_update()
    if db.scalar(query) is None:
        raise RuntimeProtocolError("execution_request_budget_scope")
    used = db.scalar(
        select(func.count(RuntimeAttempt.id))
        .join(WorkSession, WorkSession.id == RuntimeAttempt.work_session_id)
        .where(
            WorkSession.company_id == company_id,
            WorkSession.provenance_snapshot_json["project"]["id"].as_integer() == project_id,
            WorkSession.runtime_type.in_(("hermes", "openclaw")),
        )
    )
    limit = settings.runtime_project_request_limit
    valid = type(limit) is int and 1 <= limit <= 4096
    return {
        "unit": "host_request",
        "limit": limit if valid else None,
        "recorded_requests": used,
        "remaining": max(0, limit - used) if valid else None,
        "reason": UNAVAILABLE if not valid else EXHAUSTED if used >= limit else None,
    }


def admit_new_attempt(db, work):
    """Called in the transaction that inserts the new attempt, never for reuse."""
    if work.runtime_type == "mock":
        return
    if work.runtime_type not in {"hermes", "openclaw"}:
        raise RuntimeProtocolError(UNAVAILABLE)
    snapshot = work.provenance_snapshot_json
    project = snapshot.get("project") if isinstance(snapshot, dict) else None
    project_id = project.get("id") if isinstance(project, dict) else None
    if type(project_id) is not int or project_id < 1:
        raise RuntimeProtocolError("execution_request_budget_scope")
    view = project_allowance(db, company_id=work.company_id, project_id=project_id, lock=True)
    if view["reason"] is not None:
        raise RuntimeProtocolError(view["reason"])

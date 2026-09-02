"""Audit trail (v0.4 §10/§11): every lifecycle action writes an audit_logs row
with actor (nullable per spec; "user" default), before/after snapshots and the
reason given by the caller."""

from sqlalchemy.orm import Session

from app.models.lifecycle import AuditLog
from app.models.organization import Employee


def employee_snapshot(employee: Employee | None) -> dict | None:
    if employee is None:
        return None
    return {
        "id": employee.id,
        "slug": employee.slug,
        "name": employee.name,
        "role": employee.role,
        "department_id": employee.department_id,
        "lifecycle_status": employee.lifecycle_status,
        "status": employee.status,
    }


def record(
    db: Session,
    *,
    action: str,
    employee_id: int | None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str = "",
    actor: str = "user",
) -> AuditLog:
    entry = AuditLog(
        actor=actor,
        action=action,
        employee_id=employee_id,
        reason=reason,
        before_json=before,
        after_json=after,
    )
    db.add(entry)
    db.flush()
    return entry

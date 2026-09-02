"""Employee services.

v0.4: employee creation routes through the lifecycle onboarding engine
(services/lifecycle.onboard) — this module never talks to Gitea / Drive / the
filesystem for provisioning. Hard delete is dev/test-only
(EIDOLON_ALLOW_HARD_DELETE); the business flow is offboarding.
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.events.bus import bus
from app.models.organization import Employee
from app.repositories import drive as drive_repo
from app.repositories import knowledge as knowledge_repo
from app.repositories import lifecycle as lifecycle_repo
from app.repositories import organization as org_repo
from app.runtimes.gateway import gateway
from app.schemas.organization import EmployeeCreate, EmployeePatch, EmployeePerformance


async def create_employee(db: Session, payload: EmployeeCreate) -> Employee:
    """Compat shim for the pre-v0.4 POST /employees: runs the onboarding
    engine with defaults (department falls back to the role's home department)."""
    from app.schemas.lifecycle import OnboardRequest
    from app.services import lifecycle as lifecycle_service

    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=409, detail="no company seeded")
    department_id = payload.department_id
    if department_id is None:
        dept_slug = lifecycle_service.ROLE_TO_DEPARTMENT_SLUG.get(payload.role.value)
        department = (
            org_repo.get_department_by_slug(db, company.id, dept_slug) if dept_slug else None
        )
        if department is None:
            raise HTTPException(status_code=422, detail="department_id is required")
        department_id = department.id
    employee, _job = await lifecycle_service.onboard(
        db,
        OnboardRequest(
            name=payload.name,
            slug=payload.slug,
            title=payload.title,
            role=payload.role,
            department_id=department_id,
            runtime_type=payload.runtime_type,
        ),
    )
    return employee


def delete_employee(db: Session, employee: Employee) -> None:
    """Hard delete — 403 unless EIDOLON_ALLOW_HARD_DELETE=true (dev/test only).
    Business UI must use offboarding; employee + history otherwise persist."""
    if not settings.allow_hard_delete:
        raise HTTPException(
            status_code=403,
            detail="hard delete is disabled; use POST /employees/{id}/offboard",
        )
    for row in lifecycle_repo.list_employee_packages(db, employee.id):
        lifecycle_repo.delete_employee_package(db, row)
    for job in lifecycle_repo.list_jobs(db, employee_id=employee.id):
        for step in lifecycle_repo.list_steps(db, job.id):
            db.delete(step)
        db.delete(job)
    for account in lifecycle_repo.list_accounts(db, employee.id):
        db.delete(account)
    for employment in lifecycle_repo.list_employments(db, employee.id):
        db.delete(employment)
    db.delete(employee)
    db.commit()
    bus.publish(
        "employee.deleted",
        {"id": employee.id, "name": employee.name},
        company_id=employee.company_id,
    )


def update_employee(db: Session, employee: Employee, payload: EmployeePatch) -> Employee:
    data = payload.model_dump(exclude_unset=True)
    old_status = employee.status
    runtime_changed = False
    for field, value in data.items():
        if field in ("status", "runtime_type") and value is not None:
            value = value.value if hasattr(value, "value") else value
        if field == "runtime_type" and value != employee.runtime_type:
            runtime_changed = True
        setattr(employee, field, value)
    # runtime 切换只改 runtime_type/runtime_config；身份、记忆、技能、经历全部保留 (§3.4.5)
    if runtime_changed:
        gateway.drop_instance(employee.id)
    db.commit()
    db.refresh(employee)
    if employee.status != old_status:
        bus.publish(
            "employee.status_changed",
            {"id": employee.id, "name": employee.name, "from": old_status, "to": employee.status},
            company_id=employee.company_id,
            actor_employee_id=employee.id,
        )
    return employee


def get_performance(db: Session, employee_id: int) -> EmployeePerformance:
    skills = knowledge_repo.list_skills(db, employee_id)
    attempts = sum(s.attempts for s in skills)
    success = sum(s.success_count for s in skills)
    return EmployeePerformance(
        employee_id=employee_id,
        attempts=attempts,
        success_count=success,
        success_rate=round(success / attempts, 4) if attempts else 0.0,
        artifacts_count=drive_repo.count_documents_by_owner(db, employee_id),
        learning_records_count=knowledge_repo.count_learning_records(db, employee_id),
    )

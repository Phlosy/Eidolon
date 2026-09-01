"""Employee services."""

from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.events.bus import bus
from app.models.enums import EmployeeStatus
from app.models.organization import Employee
from app.repositories import knowledge as knowledge_repo
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.runtimes.gateway import gateway
from app.schemas.organization import EmployeeCreate, EmployeePatch, EmployeePerformance
from app.services import seed


def _slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")


def create_employee(db: Session, payload: EmployeeCreate) -> Employee:
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=409, detail="no company seeded")
    slug = payload.slug or _slugify(payload.name)
    if org_repo.get_employee_by_slug(db, slug):
        raise HTTPException(status_code=409, detail=f"employee slug already exists: {slug}")
    workspace_path = f"{settings.workspace_root}/{slug}"
    Path(workspace_path).mkdir(parents=True, exist_ok=True)
    employee = org_repo.create_employee(
        db,
        company_id=company.id,
        department_id=payload.department_id,
        name=payload.name,
        slug=slug,
        role=payload.role.value,
        title=payload.title,
        avatar=payload.avatar,
        status=EmployeeStatus.idle.value,
        runtime_type=payload.runtime_type.value,
        runtime_config=payload.runtime_config,
        workspace_path=workspace_path,
        memory_namespace=f"emp_{slug}",
    )
    db.commit()
    db.refresh(employee)
    seed.ensure_employee_runtime_state(db)
    bus.publish(
        "employee.created",
        {"id": employee.id, "name": employee.name, "role": employee.role},
        company_id=company.id,
        actor_employee_id=employee.id,
    )
    return employee


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
        artifacts_count=project_repo.count_artifacts_by_author(db, employee_id),
        learning_records_count=knowledge_repo.count_learning_records(db, employee_id),
    )

"""Idempotent startup seed. See docs/architecture.md §11."""

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.events.bus import bus
from app.models.enums import (
    DeploymentMode,
    EmployeeStatus,
    HealthStatus,
    RuntimeInstanceStatus,
    RuntimeType,
)
from app.models.organization import Company, Department, Employee
from app.repositories import organization as org_repo
from app.repositories import persons as person_repo
from app.repositories import runtimes as runtime_repo
from app.services import drive as drive_service

COMPANY_SLUG = "eidolon-studio"

DEPARTMENTS = [
    ("Executive", "executive"),
    ("Product", "product"),
    ("Research", "research"),
    ("Engineering", "engineering"),
    ("QA", "qa"),
]

EMPLOYEES = [
    ("Alice", "alice", "ceo", "executive", "Chief Executive Officer"),
    ("Morgan", "morgan", "product_manager", "product", "Product Manager"),
    ("Bob", "bob", "researcher", "research", "Researcher"),
    ("Charlie", "charlie", "engineer", "engineering", "Software Engineer"),
    ("Dana", "dana", "qa_engineer", "qa", "QA Engineer"),
]


# role -> default brain seed
BRAIN_DEFAULTS: dict[str, dict] = {
    "ceo": {"personality": "果断、全局视角", "goals": "确保公司交付客户价值", "curiosity": 0.4},
    "product_manager": {
        "personality": "结构化、以用户为中心",
        "goals": "把需求转化为清晰计划",
        "curiosity": 0.5,
    },
    "researcher": {"personality": "好奇、严谨", "goals": "为团队提供可靠调研", "curiosity": 0.9},
    "engineer": {"personality": "务实、注重质量", "goals": "交付可维护的实现", "curiosity": 0.6},
    "qa_engineer": {"personality": "挑剔、细致", "goals": "守住交付质量底线", "curiosity": 0.5},
}


def ensure_employee_runtime_state(db: Session) -> None:
    """Idempotently create employee_brains + mock runtime_instances rows for
    every employee (v0.2). Runs on every boot, including for pre-v0.2 seeds."""
    changed = False
    for employee in org_repo.list_employees(db):
        brain = runtime_repo.get_brain(db, employee.id)
        if brain is None:
            runtime_repo.ensure_brain(
                db, employee.id, **BRAIN_DEFAULTS.get(employee.role, {"curiosity": 0.5})
            )
            changed = True
        if runtime_repo.get_instance_for_employee(db, employee.id) is None:
            base = Path(settings.data_root) / "employees" / str(employee.id)
            for sub in ("workspace", "brain", "runtime"):
                (base / sub).mkdir(parents=True, exist_ok=True)
            runtime_repo.create_instance(
                db,
                employee_id=employee.id,
                runtime_type=RuntimeType.mock.value,
                deployment_mode=DeploymentMode.mock.value,
                image="mock-runtime",
                image_tag="latest",
                status=RuntimeInstanceStatus.running.value,
                health_status=HealthStatus.healthy.value,
                workspace_path=str(base / "workspace"),
                data_path=str(base),
                started_at=datetime.now(UTC),
            )
            changed = True
    if changed:
        db.commit()


def seed_default_company(db: Session) -> Company:
    # v0.3: the four drive zone roots always exist
    drive_service.ensure_zone_roots(db)
    db.commit()
    company = db.query(Company).filter(Company.slug == COMPANY_SLUG).first()
    if company is not None:
        ensure_employee_runtime_state(db)
        return company  # idempotent

    company = Company(
        name=settings.company_name,
        slug=COMPANY_SLUG,
        description="Autonomous AI software company (seed)",
        industry="software",
        settings={},
    )
    db.add(company)
    db.flush()

    departments: dict[str, Department] = {}
    for name, slug in DEPARTMENTS:
        department = Department(
            company_id=company.id, name=name, slug=slug, description=f"{name} department"
        )
        db.add(department)
        db.flush()
        departments[slug] = department

    employees: list[Employee] = []
    if settings.seed_demo_workforce:
        for name, slug, role, dept_slug, title in EMPLOYEES:
            workspace_path = f"{settings.workspace_root}/{slug}"
            Path(workspace_path).mkdir(parents=True, exist_ok=True)
            # PersonCore 双写（R1.0，docs/person-core-migration.md D2）：先建 person
            # 再建 employee。persons.slug 是唯一权威，employees.slug/username 是同源镜像
            # （username 与 onboard 口径一致 = slug，不再依赖 seed_lifecycle 的事后回填）。
            person = person_repo.create_person(db, slug=slug, name=name, avatar="", username=slug)
            employee = Employee(
                person_id=person.id,
                company_id=company.id,
                username=slug,
                department_id=departments[dept_slug].id,
                name=name,
                slug=slug,
                role=role,
                title=title,
                status=EmployeeStatus.idle.value,
                runtime_type=RuntimeType.mock.value,
                runtime_config={},
                workspace_path=workspace_path,
                memory_namespace=f"emp_{slug}",
            )
            db.add(employee)
            db.flush()
            employees.append(employee)

    db.commit()
    db.refresh(company)

    ensure_employee_runtime_state(db)

    bus.publish(
        "company.created",
        {"id": company.id, "name": company.name, "slug": company.slug},
        company_id=company.id,
    )
    for employee in employees:
        bus.publish(
            "employee.created",
            {"id": employee.id, "name": employee.name, "role": employee.role},
            company_id=company.id,
            actor_employee_id=employee.id,
        )
    return company

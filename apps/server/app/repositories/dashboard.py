"""Company-scoped aggregate queries with a constant number of round trips."""

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.drive import DriveNode
from app.models.enums import DriveNodeKind, ProjectStatus, TaskStatus
from app.models.knowledge import Skill
from app.models.organization import Employee
from app.models.project import Project, Task
from app.repositories import persons as person_repo


def employee_status_counts(db: Session, company_id: int) -> dict[str, int]:
    return dict(
        db.execute(
            select(Employee.status, func.count(Employee.id))
            .where(Employee.company_id == company_id)
            .group_by(Employee.status)
        ).all()
    )


def project_status_counts(db: Session, company_id: int) -> dict[str, int]:
    return dict(
        db.execute(
            select(Project.status, func.count(Project.id))
            .where(Project.company_id == company_id)
            .group_by(Project.status)
        ).all()
    )


def task_status_counts(db: Session, company_id: int) -> dict[str, int]:
    return dict(
        db.execute(
            select(Task.status, func.count(Task.id))
            .join(Project, Task.project_id == Project.id)
            .where(Project.company_id == company_id)
            .group_by(Task.status)
        ).all()
    )


def count_documents(db: Session, company_id: int) -> int:
    return db.scalar(
        select(func.count(DriveNode.id)).where(
            DriveNode.company_id == company_id, DriveNode.kind == DriveNodeKind.document.value
        )
    )


def sum_attempts(db: Session, company_id: int) -> int:
    # Match the sum of employee performance.attempts, including legacy fallback.
    return db.scalar(
        select(func.coalesce(func.sum(Skill.attempts), 0))
        .select_from(Skill)
        .join(
            Employee,
            person_repo.company_read_criterion(company_id, Skill.person_id, Skill.employee_id),
        )
    )


def project_summaries(db: Session, company_id: int) -> list[dict]:
    # Match ActiveProjectBoard: newest four nonterminal projects, including waiting
    # for management. This preview limit never applies to the company-wide counts.
    recent = (
        select(Project.id)
        .where(
            Project.company_id == company_id,
            Project.status.not_in(
                (
                    ProjectStatus.completed.value,
                    ProjectStatus.cancelled.value,
                    ProjectStatus.rejected.value,
                )
            ),
        )
        .order_by(Project.id.desc())
        .limit(4)
        .subquery()
    )
    rows = db.execute(
        select(
            recent.c.id.label("project_id"),
            func.count(Task.id).label("tasks_total"),
            func.sum(case((Task.status == TaskStatus.done.value, 1), else_=0)).label(
                "tasks_completed"
            ),
            func.sum(
                case(
                    (Task.status.in_((TaskStatus.failed.value, TaskStatus.rejected.value)), 1),
                    else_=0,
                )
            ).label("blockers"),
        )
        .outerjoin(Task, Task.project_id == recent.c.id)
        .group_by(recent.c.id)
        .order_by(recent.c.id.desc())
    ).mappings()
    return [dict(row) for row in rows]

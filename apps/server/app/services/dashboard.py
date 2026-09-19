"""Dashboard metrics preserve the existing UI's status and attempt definitions."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.enums import EmployeeStatus, ProjectStatus, TaskStatus
from app.repositories import dashboard as dashboard_repo
from app.repositories import organization as org_repo
from app.schemas.dashboard import DashboardSummary

_ACTIVE_PROJECT_STATUSES = (
    ProjectStatus.requested.value,
    ProjectStatus.planning.value,
    ProjectStatus.in_progress.value,
    ProjectStatus.in_review.value,
)


def get_summary(db: Session) -> DashboardSummary:
    # Session identity wins. Auth-off anonymous requests retain single-company mode
    # by selecting only the default company, never aggregating the whole database.
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    employees = dashboard_repo.employee_status_counts(db, company.id)
    projects = dashboard_repo.project_status_counts(db, company.id)
    tasks = dashboard_repo.task_status_counts(db, company.id)
    employees_total = sum(employees.values())
    return DashboardSummary(
        company_id=company.id,
        employees_online=employees_total - employees.get(EmployeeStatus.offline.value, 0),
        employees_total=employees_total,
        active_projects=sum(projects.get(status, 0) for status in _ACTIVE_PROJECT_STATUSES),
        completed_projects=projects.get(ProjectStatus.completed.value, 0),
        tasks_in_progress=tasks.get(TaskStatus.in_progress.value, 0)
        + tasks.get(TaskStatus.in_review.value, 0),
        tasks_total=sum(tasks.values()),
        tasks_completed=tasks.get(TaskStatus.done.value, 0),
        documents_count=dashboard_repo.count_documents(db, company.id),
        total_attempts=dashboard_repo.sum_attempts(db, company.id),
        project_summaries=dashboard_repo.project_summaries(db, company.id),
    )

"""Project services: order creation (docs/architecture.md §5)."""

from collections import defaultdict
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.base import utcnow
from app.models.enums import EmployeeRole, MilestoneStatus, ProjectStatus, TaskKind, TaskStatus
from app.models.project import Milestone, Project, Task
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.schemas.project import (
    GraphEdge,
    GraphNode,
    MilestoneOut,
    ProjectCreate,
    ProjectDetail,
    ProjectGraph,
    ProjectOut,
    ProjectTimeline,
)
from app.services import artifacts as artifact_service
from app.services import drive as drive_service
from app.services import tasks as task_service


def create_order(db: Session, payload: ProjectCreate) -> Project:
    """POST /projects = 创建 Order：project.status=requested + order_review task (CEO)."""
    if payload.is_structured:
        from app.services.project_delivery import create_structured_project

        return create_structured_project(db, payload)
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=409, detail="no company seeded")
    pm = org_repo.get_employee_by_role(db, company.id, EmployeeRole.product_manager.value)
    ceo = org_repo.get_employee_by_role(db, company.id, EmployeeRole.ceo.value)
    schedule_start = utcnow()
    project = project_repo.create_project(
        db,
        company_id=company.id,
        name=payload.name,
        description=payload.description,
        goal=payload.goal,
        status=ProjectStatus.requested.value,
        owner_id=pm.id if pm else None,
        source_order_text=payload.description,
        planned_start_at=schedule_start,
        planned_end_at=schedule_start + timedelta(days=18),
    )
    task = task_service.create_task(
        db,
        project_id=project.id,
        title=f"订单评审：{project.name}",
        kind=TaskKind.order_review.value,
        assignee_id=ceo.id if ceo else None,
        status=TaskStatus.todo.value,
        description=payload.description,
        acceptance_criteria="需求清晰、范围可控即可批准",
        priority=10,
        sequence=0,
        planned_start_at=schedule_start,
        planned_end_at=schedule_start + timedelta(days=1),
    )
    db.commit()
    db.refresh(project)
    # v0.3: every project is a drive folder tree (drive/projects/{slug}/...)
    drive_service.ensure_project_folders(db, project)
    db.commit()
    bus.publish(
        "project.created",
        {"id": project.id, "name": project.name, "status": project.status},
        company_id=company.id,
        project_id=project.id,
    )
    task_service.publish_task_created(task, company.id)
    from app.workflow.orchestrator import orchestrator

    orchestrator.notify({"type": "dispatch"})
    return project


def _project_timeline(
    project: Project, milestones: list[Milestone], tasks: list[Task]
) -> ProjectTimeline:
    milestone_outputs = []
    for milestone in milestones:
        milestone_tasks = [task for task in tasks if task.milestone_id == milestone.id]
        output = MilestoneOut.model_validate(milestone)
        if milestone_tasks and all(
            task.status == TaskStatus.done.value for task in milestone_tasks
        ):
            output.status = MilestoneStatus.completed.value
        elif any(
            task.status
            in {
                TaskStatus.todo.value,
                TaskStatus.in_progress.value,
                TaskStatus.in_review.value,
            }
            for task in milestone_tasks
        ):
            output.status = MilestoneStatus.in_progress.value
        milestone_outputs.append(output)
    return ProjectTimeline(
        **ProjectOut.model_validate(project).model_dump(),
        milestones=milestone_outputs,
        tasks=[task_service.task_out(t) for t in tasks],
    )


def get_project_portfolio(db: Session) -> list[ProjectTimeline]:
    projects = project_repo.list_projects(db)
    project_ids = [project.id for project in projects]
    milestones = project_repo.list_milestones_for_projects(db, project_ids)
    tasks = project_repo.list_tasks_for_projects(db, project_ids)
    milestones_by_project: dict[int, list[Milestone]] = defaultdict(list)
    tasks_by_project: dict[int, list[Task]] = defaultdict(list)
    for milestone in milestones:
        milestones_by_project[milestone.project_id].append(milestone)
    for task in tasks:
        tasks_by_project[task.project_id].append(task)
    return [
        _project_timeline(
            project,
            milestones_by_project[project.id],
            tasks_by_project[project.id],
        )
        for project in projects
    ]


def get_project_detail(db: Session, project: Project) -> ProjectDetail:
    tasks = project_repo.list_tasks(db, project.id)
    milestones = project_repo.list_milestones(db, project.id)
    timeline = _project_timeline(project, milestones, tasks)
    # v0.3: artifacts are a typed view over the project's drive documents
    artifacts = artifact_service.list_artifact_nodes(db, project_id=project.id)
    return ProjectDetail(
        **timeline.model_dump(),
        artifacts=[artifact_service.artifact_out(db, a) for a in artifacts],
    )


def get_project_graph(db: Session, project: Project) -> ProjectGraph:
    tasks = project_repo.list_tasks(db, project.id)
    dependencies = project_repo.list_dependencies(db, project.id)
    return ProjectGraph(
        nodes=[
            GraphNode(
                id=str(t.id),
                type=t.kind,
                label=f"#EID-{t.id} {t.title}",
                status=t.status,
            )
            for t in tasks
        ],
        edges=[
            GraphEdge(source=str(dep.depends_on_id), target=str(dep.task_id))
            for dep in dependencies
        ],
    )

"""Project services: order creation (docs/architecture.md §5)."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.enums import EmployeeRole, ProjectStatus, TaskKind, TaskStatus
from app.models.project import Project
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.schemas.project import (
    ArtifactOut,
    GraphEdge,
    GraphNode,
    MilestoneOut,
    ProjectCreate,
    ProjectDetail,
    ProjectGraph,
    ProjectOut,
)
from app.services import tasks as task_service


def create_order(db: Session, payload: ProjectCreate) -> Project:
    """POST /projects = 创建 Order：project.status=requested + order_review task (CEO)."""
    company = org_repo.get_default_company(db)
    if company is None:
        raise HTTPException(status_code=409, detail="no company seeded")
    pm = org_repo.get_employee_by_role(db, company.id, EmployeeRole.product_manager.value)
    ceo = org_repo.get_employee_by_role(db, company.id, EmployeeRole.ceo.value)
    project = project_repo.create_project(
        db,
        company_id=company.id,
        name=payload.name,
        description=payload.description,
        goal=payload.goal,
        status=ProjectStatus.requested.value,
        owner_id=pm.id if pm else None,
        source_order_text=payload.description,
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
    )
    db.commit()
    db.refresh(project)
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


def get_project_detail(db: Session, project: Project) -> ProjectDetail:
    tasks = project_repo.list_tasks(db, project.id)
    milestones = project_repo.list_milestones(db, project.id)
    artifacts = project_repo.list_artifacts(db, project_id=project.id)
    base = ProjectOut.model_validate(project)
    return ProjectDetail(
        **base.model_dump(),
        milestones=[MilestoneOut.model_validate(m) for m in milestones],
        tasks=[task_service.task_out(t) for t in tasks],
        artifacts=[ArtifactOut.model_validate(a) for a in artifacts],
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

"""Task services: creation helper + state machine (docs/architecture.md §3.4.4).

Legal transitions: backlog→todo→in_progress→in_review→done/failed/rejected,
plus rejected→todo (QA 驳回重做). The orchestrator may use force=True for
internal rework paths (e.g. done→todo on QA rejection).
"""

from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.base import utcnow
from app.models.enums import TaskStatus
from app.models.project import Task
from app.repositories import project as project_repo

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.backlog.value: {TaskStatus.todo.value},
    TaskStatus.todo.value: {TaskStatus.in_progress.value},
    TaskStatus.in_progress.value: {TaskStatus.in_review.value, TaskStatus.failed.value},
    TaskStatus.in_review.value: {
        TaskStatus.done.value,
        TaskStatus.failed.value,
        TaskStatus.rejected.value,
    },
    TaskStatus.rejected.value: {TaskStatus.todo.value},
    TaskStatus.done.value: set(),
    TaskStatus.failed.value: set(),
}


class InvalidTransitionError(ValueError):
    pass


def transition_task(db: Session, task: Task, target: str, *, force: bool = False) -> Task:
    target = TaskStatus(target).value
    if task.status == target:
        return task
    if not force and target not in ALLOWED_TRANSITIONS.get(task.status, set()):
        raise InvalidTransitionError(f"invalid task transition: {task.status} -> {target}")
    task.status = target
    if target == TaskStatus.in_progress.value and task.actual_start_at is None:
        task.actual_start_at = utcnow()
    if target in {
        TaskStatus.done.value,
        TaskStatus.failed.value,
        TaskStatus.rejected.value,
    }:
        task.actual_end_at = utcnow()
    elif target == TaskStatus.todo.value:
        task.actual_end_at = None
    db.flush()
    return task


def create_task(
    db: Session,
    *,
    project_id: int,
    title: str,
    kind: str,
    assignee_id: int | None,
    status: str = TaskStatus.backlog.value,
    milestone_id: int | None = None,
    description: str = "",
    acceptance_criteria: str = "",
    priority: int = 0,
    sequence: int = 0,
    depends_on: list[int] | None = None,
    planned_start_at=None,
    planned_end_at=None,
) -> Task:
    task = project_repo.create_task(
        db,
        project_id=project_id,
        title=title,
        kind=kind,
        assignee_id=assignee_id,
        status=status,
        milestone_id=milestone_id,
        description=description,
        acceptance_criteria=acceptance_criteria,
        priority=priority,
        sequence=sequence,
        planned_start_at=planned_start_at,
        planned_end_at=planned_end_at,
    )
    for dep_id in depends_on or []:
        project_repo.add_dependency(db, task_id=task.id, depends_on_id=dep_id)
    return task


def publish_task_created(task: Task, company_id: int) -> None:
    bus.publish(
        "task.created",
        {"id": task.id, "title": task.title, "kind": task.kind, "status": task.status},
        company_id=company_id,
        project_id=task.project_id,
        task_id=task.id,
        actor_employee_id=task.assignee_id,
    )
    if task.assignee_id is not None:
        bus.publish(
            "task.assigned",
            {"id": task.id, "title": task.title, "assignee_id": task.assignee_id},
            company_id=company_id,
            project_id=task.project_id,
            task_id=task.id,
            actor_employee_id=task.assignee_id,
        )


def task_dependencies(task: Task) -> list[int]:
    return [dep.depends_on_id for dep in task.dependencies]


def task_out(task: Task):
    """Explicit TaskOut construction — dependencies come from the association table."""
    from app.schemas.project import TaskOut

    return TaskOut(
        id=task.id,
        project_id=task.project_id,
        milestone_id=task.milestone_id,
        title=task.title,
        description=task.description,
        kind=task.kind,
        status=task.status,
        priority=task.priority,
        assignee_id=task.assignee_id,
        acceptance_criteria=task.acceptance_criteria,
        sequence=task.sequence,
        planned_start_at=task.planned_start_at,
        planned_end_at=task.planned_end_at,
        actual_start_at=task.actual_start_at,
        actual_end_at=task.actual_end_at,
        phase_id=task.phase_id,
        dependencies=task_dependencies(task),
        created_at=task.created_at,
        updated_at=task.updated_at,
    )

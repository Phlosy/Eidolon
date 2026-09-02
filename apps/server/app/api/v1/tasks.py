"""/tasks"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.events.bus import bus
from app.models.enums import TaskStatus
from app.repositories import project as project_repo
from app.schemas.project import TaskOut, TaskPatch
from app.services import tasks as task_service
from app.services.schedules import InvalidScheduleError, apply_schedule_patch


def _task_out(task) -> TaskOut:
    return task_service.task_out(task)


router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task_id: int, db: Session = Depends(get_db)) -> TaskOut:
    task = project_repo.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _task_out(task)


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(task_id: int, payload: TaskPatch, db: Session = Depends(get_db)) -> TaskOut:
    task = project_repo.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    data = payload.model_dump(exclude_unset=True)
    new_status = data.pop("status", None)
    schedule_data = {
        field: data.pop(field) for field in ("planned_start_at", "planned_end_at") if field in data
    }
    try:
        apply_schedule_patch(task, schedule_data)
    except InvalidScheduleError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for field, value in data.items():
        setattr(task, field, value)
    if new_status is not None:
        try:
            task_service.transition_task(db, task, new_status.value)
        except task_service.InvalidTransitionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(task)
    project = project_repo.get_project(db, task.project_id)
    if project is not None:
        bus.publish(
            "task.updated",
            {"id": task.id, "status": task.status, "assignee_id": task.assignee_id},
            company_id=project.company_id,
            project_id=project.id,
            task_id=task.id,
            actor_employee_id=task.assignee_id,
        )
    if new_status is not None:
        from app.workflow.orchestrator import orchestrator

        if new_status == TaskStatus.todo:
            orchestrator.notify({"type": "dispatch"})
        elif new_status in (TaskStatus.done, TaskStatus.failed):
            orchestrator.notify(
                {
                    "type": "task_finished",
                    "task_id": task.id,
                    "success": new_status == TaskStatus.done,
                }
            )
    return _task_out(task)

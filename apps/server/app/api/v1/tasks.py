"""/tasks"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.enums import TaskStatus
from app.repositories import project as project_repo
from app.schemas.project import TaskOut, TaskPatch
from app.services import tasks as task_service


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
    for field, value in data.items():
        setattr(task, field, value)
    if new_status is not None:
        try:
            task_service.transition_task(db, task, new_status.value)
        except task_service.InvalidTransitionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    db.refresh(task)
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

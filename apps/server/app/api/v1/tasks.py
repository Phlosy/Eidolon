"""/tasks"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.events.bus import bus
from app.models.enums import TaskStatus
from app.repositories import project as project_repo
from app.schemas.project import TaskOut, TaskPatch
from app.schemas.work import (
    TaskArtifactReportOut,
    TaskInputsIn,
    TaskInputsOut,
)
from app.services import tasks as task_service
from app.services.schedules import InvalidScheduleError, apply_schedule_patch
from app.work import handoff


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


# ---------------------------------------------------------------------------
# M2.6 Artifact Handoff（设计 §14c，H1–H8）
#
# 三个端点、一个服务层口径（`app/work/handoff.py`）：
#   · 读：产出 / 使用 / 声明 / 上游链（G2）
#   · 写：声明输入（人类管理动作）—— 与 Agent 工具**同一段**应用服务（T11）
#   · 写：显式消费某个已完成任务的产物 —— 未完成的产出一律 **422**（H8/G3）
# ---------------------------------------------------------------------------


def _task_or_404(db: Session, task_id: int):
    task = project_repo.get_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@router.get("/{task_id}/artifacts", response_model=TaskArtifactReportOut)
def get_task_artifacts(task_id: int, db: Session = Depends(get_db)) -> TaskArtifactReportOut:
    """一个任务的交付物全景：产出了什么、用了谁的、上游链（≥2 跳）。"""
    _task_or_404(db, task_id)
    return TaskArtifactReportOut(**handoff.task_artifact_report(db, task_id).as_dict())


@router.post("/{task_id}/inputs", response_model=TaskInputsOut)
def declare_task_inputs(
    task_id: int, payload: TaskInputsIn, db: Session = Depends(get_db)
) -> TaskInputsOut:
    """声明"这个任务要用谁的产品"（人类管理动作，M2.6/H4）。"""
    task = _task_or_404(db, task_id)
    try:
        handoff.declare_inputs(db, task=task, source_task_ids=payload.source_task_ids)
    except handoff.LineageViolation as exc:
        # H5：声明必须有顺序保证（上游是 DAG 祖先）—— 结构问题 ⇒ 422，不是 500
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TaskInputsOut(
        task_id=int(task.id),
        declared_inputs=[item.as_dict() for item in handoff.declared_inputs(db, int(task.id))],
    )


@router.post("/{task_id}/artifacts/{artifact_id}/consume", response_model=TaskArtifactReportOut)
def consume_task_artifact(
    task_id: int, artifact_id: int, db: Session = Depends(get_db)
) -> TaskArtifactReportOut:
    """把一个**已完成**任务的产物登记为这个任务的输入（H3/H8）。"""
    task = _task_or_404(db, task_id)
    try:
        handoff.consume_artifact(db, task=task, artifact_id=artifact_id, reason="explicit")
    except handoff.LineageViolation as exc:
        # G3：引用未完成的 Task 产物 ⇒ 422（系统拒绝，而不是默默接受）
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TaskArtifactReportOut(**handoff.task_artifact_report(db, task_id).as_dict())

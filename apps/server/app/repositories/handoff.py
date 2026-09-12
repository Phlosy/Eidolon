"""M2.6 Artifact Handoff 的查询层（`task_inputs` / `artifact_links`）。

只有查询与最小写入原语；**业务规则**（谁能声明谁、什么能消费）在
`app/work/handoff.py` —— 那里是唯一口径，HTTP / 工具 / 执行面都走它。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.drive import DriveNode
from app.models.enums import DriveNodeKind, DriveZone
from app.models.handoff import ArtifactLink, TaskInput

# ---------------------------------------------------------------------------
# 输入声明
# ---------------------------------------------------------------------------


def list_inputs(db: Session, task_id: int) -> list[TaskInput]:
    return list(
        db.scalars(
            select(TaskInput)
            .where(TaskInput.task_id == int(task_id))
            .order_by(TaskInput.source_task_id)
        )
    )


def list_dependents(db: Session, source_task_id: int) -> list[TaskInput]:
    """谁声明消费了这个 Task 的产出（反向查询：用来做"产物被谁用了"）。"""
    return list(
        db.scalars(
            select(TaskInput)
            .where(TaskInput.source_task_id == int(source_task_id))
            .order_by(TaskInput.task_id)
        )
    )


def add_input(db: Session, *, task_id: int, source_task_id: int) -> TaskInput:
    row = TaskInput(task_id=int(task_id), source_task_id=int(source_task_id))
    db.add(row)
    db.flush()
    return row


def delete_input(db: Session, row: TaskInput) -> None:
    db.delete(row)


# ---------------------------------------------------------------------------
# 使用关系
# ---------------------------------------------------------------------------


def list_artifact_links(
    db: Session, *, task_id: int | None = None, artifact_id: int | None = None
) -> list[ArtifactLink]:
    stmt = select(ArtifactLink).order_by(ArtifactLink.id)
    if task_id is not None:
        stmt = stmt.where(ArtifactLink.task_id == int(task_id))
    if artifact_id is not None:
        stmt = stmt.where(ArtifactLink.artifact_id == int(artifact_id))
    return list(db.scalars(stmt))


def add_artifact_link(
    db: Session,
    *,
    artifact_id: int,
    task_id: int,
    role: str,
    work_session_id: int | None = None,
    actor_employee_id: int | None = None,
    reason: str = "",
) -> ArtifactLink:
    row = ArtifactLink(
        artifact_id=int(artifact_id),
        task_id=int(task_id),
        role=role,
        work_session_id=work_session_id,
        actor_employee_id=actor_employee_id,
        reason=reason,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# 产出归属（H2）
# ---------------------------------------------------------------------------


def list_artifacts_for_task(db: Session, task_id: int) -> list[DriveNode]:
    """这个 Task 产出的交付物（项目区文档，按产出归属列反查）。"""
    return list(
        db.scalars(
            select(DriveNode)
            .where(
                DriveNode.task_id == int(task_id),
                DriveNode.kind == DriveNodeKind.document.value,
                DriveNode.zone == DriveZone.projects.value,
            )
            .order_by(DriveNode.id)
        )
    )


__all__ = [
    "add_artifact_link",
    "add_input",
    "delete_input",
    "list_artifact_links",
    "list_artifacts_for_task",
    "list_dependents",
    "list_inputs",
]

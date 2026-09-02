"""Project repositories: projects / milestones / tasks / sessions / artifacts / messages."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.request_context import get_request_identity
from app.models.enums import WorkSessionStatus
from app.models.project import (
    Artifact,
    Message,
    Milestone,
    Project,
    Task,
    TaskDependency,
    WorkSession,
)


def list_projects(db: Session, company_id: int | None = None) -> list[Project]:
    identity = get_request_identity()
    if company_id is None and identity is not None:
        company_id = identity.company_id
    stmt = select(Project).order_by(Project.id.desc())
    if company_id is not None:
        stmt = stmt.where(Project.company_id == company_id)
    return list(db.scalars(stmt))


def get_project(db: Session, project_id: int) -> Project | None:
    stmt = select(Project).where(Project.id == project_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(Project.company_id == identity.company_id)
    return db.scalar(stmt)


def create_project(db: Session, **fields) -> Project:
    project = Project(**fields)
    db.add(project)
    db.flush()
    return project


def create_milestone(db: Session, **fields) -> Milestone:
    milestone = Milestone(**fields)
    db.add(milestone)
    db.flush()
    return milestone


def list_milestones(db: Session, project_id: int) -> list[Milestone]:
    return list(
        db.scalars(
            select(Milestone).where(Milestone.project_id == project_id).order_by(Milestone.order)
        )
    )


def get_milestone(db: Session, milestone_id: int) -> Milestone | None:
    stmt = select(Milestone).where(Milestone.id == milestone_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.join(Project, Milestone.project_id == Project.id).where(
            Project.company_id == identity.company_id
        )
    return db.scalar(stmt)


def list_milestones_for_projects(db: Session, project_ids: list[int]) -> list[Milestone]:
    if not project_ids:
        return []
    return list(
        db.scalars(
            select(Milestone)
            .where(Milestone.project_id.in_(project_ids))
            .order_by(Milestone.project_id, Milestone.order)
        )
    )


def get_task(db: Session, task_id: int) -> Task | None:
    stmt = select(Task).where(Task.id == task_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.join(Project, Task.project_id == Project.id).where(
            Project.company_id == identity.company_id
        )
    return db.scalar(stmt)


def list_tasks(db: Session, project_id: int) -> list[Task]:
    return list(
        db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.sequence))
    )


def list_tasks_for_projects(db: Session, project_ids: list[int]) -> list[Task]:
    if not project_ids:
        return []
    return list(
        db.scalars(
            select(Task)
            .options(selectinload(Task.dependencies))
            .where(Task.project_id.in_(project_ids))
            .order_by(Task.project_id, Task.sequence)
        )
    )


def list_tasks_by_status(db: Session, status: str) -> list[Task]:
    return list(db.scalars(select(Task).where(Task.status == status).order_by(Task.id)))


def create_task(db: Session, **fields) -> Task:
    task = Task(**fields)
    db.add(task)
    db.flush()
    return task


def add_dependency(db: Session, task_id: int, depends_on_id: int) -> TaskDependency:
    dep = TaskDependency(task_id=task_id, depends_on_id=depends_on_id)
    db.add(dep)
    db.flush()
    return dep


def list_dependencies(db: Session, project_id: int) -> list[TaskDependency]:
    task_ids = select(Task.id).where(Task.project_id == project_id)
    return list(db.scalars(select(TaskDependency).where(TaskDependency.task_id.in_(task_ids))))


def create_work_session(db: Session, **fields) -> WorkSession:
    session = WorkSession(**fields)
    db.add(session)
    db.flush()
    return session


def get_running_session_for_employee(db: Session, employee_id: int) -> WorkSession | None:
    return db.scalars(
        select(WorkSession).where(
            WorkSession.employee_id == employee_id,
            WorkSession.status == WorkSessionStatus.running.value,
        )
    ).first()


def get_running_session_for_task(db: Session, task_id: int) -> WorkSession | None:
    return db.scalars(
        select(WorkSession)
        .where(
            WorkSession.task_id == task_id,
            WorkSession.status == WorkSessionStatus.running.value,
        )
        .order_by(WorkSession.id.desc())
    ).first()


def list_artifacts(
    db: Session, project_id: int | None = None, type: str | None = None
) -> list[Artifact]:
    stmt = select(Artifact).order_by(Artifact.id.desc())
    if project_id is not None:
        stmt = stmt.where(Artifact.project_id == project_id)
    if type is not None:
        stmt = stmt.where(Artifact.type == type)
    return list(db.scalars(stmt))


def get_artifact(db: Session, artifact_id: int) -> Artifact | None:
    return db.get(Artifact, artifact_id)


def create_artifact(db: Session, **fields) -> Artifact:
    artifact = Artifact(**fields)
    db.add(artifact)
    db.flush()
    return artifact


def count_artifacts_by_author(db: Session, author_id: int) -> int:
    return len(list(db.scalars(select(Artifact.id).where(Artifact.author_id == author_id))))


def list_messages(db: Session, project_id: int | None = None) -> list[Message]:
    stmt = select(Message).order_by(Message.id.desc()).limit(100)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(Message.company_id == identity.company_id)
    if project_id is not None:
        stmt = stmt.where(Message.project_id == project_id)
    return list(db.scalars(stmt))


def create_message(db: Session, **fields) -> Message:
    message = Message(**fields)
    db.add(message)
    db.flush()
    return message

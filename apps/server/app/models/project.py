"""Project domain: Project / Milestone / Task / TaskDependency / WorkSession / Artifact / Message.

See docs/architecture.md §3.2.
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    ArtifactStatus,
    ArtifactType,
    MilestoneStatus,
    ProjectStatus,
    TaskKind,
    TaskStatus,
    WorkSessionStatus,
)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default=ProjectStatus.requested.value)
    goal: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    source_order_text: Mapped[str] = mapped_column(Text, default="")
    planned_start_at: Mapped[datetime | None] = mapped_column(nullable=True)
    planned_end_at: Mapped[datetime | None] = mapped_column(nullable=True)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    priority: Mapped[str] = mapped_column(String(30), default="medium")
    customer: Mapped[str] = mapped_column(String(200), default="")
    background: Mapped[str] = mapped_column(Text, default="")
    objectives: Mapped[list] = mapped_column(JSON, default=list)
    technical_requirements: Mapped[list] = mapped_column(JSON, default=list)
    constraints: Mapped[list] = mapped_column(JSON, default=list)
    deliverables: Mapped[list] = mapped_column(JSON, default=list)
    review_configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    participants: Mapped[dict] = mapped_column(JSON, default=dict)
    tutorial_accelerated: Mapped[bool] = mapped_column(Boolean, default=False)

    milestones: Mapped[list["Milestone"]] = relationship(back_populates="project")
    tasks: Mapped[list["Task"]] = relationship(back_populates="project")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="project")


class Milestone(TimestampMixin, Base):
    __tablename__ = "milestones"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default=MilestoneStatus.pending.value)
    order: Mapped[int] = mapped_column(default=0)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    planned_start_at: Mapped[datetime | None] = mapped_column(nullable=True)
    planned_end_at: Mapped[datetime | None] = mapped_column(nullable=True)

    project: Mapped[Project] = relationship(back_populates="milestones")


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    milestone_id: Mapped[int | None] = mapped_column(ForeignKey("milestones.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(50), default=TaskKind.general.value)
    status: Mapped[str] = mapped_column(String(50), default=TaskStatus.backlog.value)
    priority: Mapped[int] = mapped_column(default=0)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    sequence: Mapped[int] = mapped_column(default=0)
    planned_start_at: Mapped[datetime | None] = mapped_column(nullable=True)
    planned_end_at: Mapped[datetime | None] = mapped_column(nullable=True)
    actual_start_at: Mapped[datetime | None] = mapped_column(nullable=True)
    actual_end_at: Mapped[datetime | None] = mapped_column(nullable=True)
    phase_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_phases.id"), nullable=True, index=True
    )

    project: Mapped[Project] = relationship(back_populates="tasks")
    dependencies: Mapped[list["TaskDependency"]] = relationship(
        back_populates="task", foreign_keys="TaskDependency.task_id"
    )
    sessions: Mapped[list["WorkSession"]] = relationship(back_populates="task")


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), primary_key=True)

    task: Mapped[Task] = relationship(foreign_keys=[task_id], back_populates="dependencies")


class WorkSession(TimestampMixin, Base):
    __tablename__ = "work_sessions"

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    runtime_type: Mapped[str] = mapped_column(String(50))
    runtime_session_ref: Mapped[str] = mapped_column(String(200), default="")
    # P11：本次会话使用的行为策略快照（可解释"为什么这次主动请求 Peer Review"）
    behavior_snapshot_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    behavior_snapshot_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(50), default=WorkSessionStatus.running.value)
    summary: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cost: Mapped[dict] = mapped_column(JSON, default=dict)
    # v0.2: link to the runtime instance + provider/model actually used
    runtime_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("runtime_instances.id"), nullable=True
    )
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("providers.id"), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped[Task] = relationship(back_populates="sessions")


class Artifact(TimestampMixin, Base):
    __tablename__ = "artifacts"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(50), default=ArtifactType.other.value)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, default="")
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    work_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_sessions.id"), nullable=True
    )
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(50), default=ArtifactStatus.draft.value)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)

    project: Mapped[Project] = relationship(back_populates="artifacts")


class Message(TimestampMixin, Base):
    __tablename__ = "messages"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("employees.id"))
    recipient_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(100), default="general")
    content: Mapped[str] = mapped_column(Text, default="")

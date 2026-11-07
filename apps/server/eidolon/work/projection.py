"""Read model for work: four tables, all derived from ``work.*`` facts, rebuildable.
Readiness is *not* a column (RD1): compute it with ``graph.resolve_ready``."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, Session, mapped_column

from eidolon.db import Base
from eidolon.kernel.facts import Fact
from eidolon.work import facts as F


class MissionRow(Base):
    __tablename__ = "work_missions"
    __table_args__ = (Index("ix_work_missions_company", "company_id", "status"),)
    mission_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    success_criteria: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    constraints: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    budget: Mapped[int | None] = mapped_column(Integer)
    deadline: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decision_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)


class ProjectRow(Base):
    __tablename__ = "work_projects"
    __table_args__ = (Index("ix_work_projects_company", "company_id", "status"),)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    mission_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    budget: Mapped[int] = mapped_column(Integer, nullable=False)
    manager: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    plan_decision_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)


class TaskRow(Base):
    __tablename__ = "work_tasks"
    __table_args__ = (
        Index("ix_work_tasks_project", "project_id", "status"),
        Index("ix_work_tasks_company_status", "company_id", "status"),
        Index("ix_work_tasks_assignee", "company_id", "assignee"),
    )
    task_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    role: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    assignee: Mapped[str | None] = mapped_column(String(200))
    depends_on: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    inputs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    rework_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    reviews: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)


class ArtifactRow(Base):
    __tablename__ = "work_artifacts"
    __table_args__ = (
        Index("ix_work_artifacts_task", "task_id"),
        Index("ix_work_artifacts_project", "project_id"),
    )
    artifact_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    task_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    blob_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    uses: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False)


TABLES = ("work_missions", "work_projects", "work_tasks", "work_artifacts")


class WorkProjection:
    name = "work_items"
    handles = F.KINDS

    def handle(self, db: Session, fact: Fact) -> None:
        p = fact.payload
        k = fact.kind
        if k == F.MISSION_CREATED:
            db.execute(
                insert(MissionRow)
                .values(
                    mission_id=UUID(p["mission_id"]),
                    company_id=fact.company_id,
                    title=p["title"],
                    goal=p["goal"],
                    success_criteria=p.get("success_criteria") or [],
                    constraints=p.get("constraints") or [],
                    budget=p.get("budget"),
                    deadline=p.get("deadline"),
                    status="proposed",
                    created_at=fact.created_at,
                    updated_at=fact.created_at,
                    last_seq=fact.seq,
                )
                .on_conflict_do_nothing(index_elements=["mission_id"])
            )
        elif k in (F.MISSION_ACCEPTED, F.MISSION_REJECTED):
            row = db.get(MissionRow, UUID(p["mission_id"]))
            if _fresh(row, fact):
                row.status = "accepted" if k == F.MISSION_ACCEPTED else "rejected"
                row.decision_id = UUID(p["decision_id"])
                _touch(row, fact)
        elif k == F.PROJECT_CREATED:
            db.execute(
                insert(ProjectRow)
                .values(
                    project_id=UUID(p["project_id"]),
                    company_id=fact.company_id,
                    mission_id=UUID(p["mission_id"]) if p.get("mission_id") else None,
                    title=p["title"],
                    budget=p["budget"],
                    manager=p.get("manager"),
                    status="planning",
                    created_at=fact.created_at,
                    updated_at=fact.created_at,
                    last_seq=fact.seq,
                )
                .on_conflict_do_nothing(index_elements=["project_id"])
            )
        elif k in (F.PROJECT_PLAN_APPROVED, F.PROJECT_DELIVERED, F.PROJECT_CANCELLED):
            row = db.get(ProjectRow, UUID(p["project_id"]))
            if _fresh(row, fact):
                if k == F.PROJECT_PLAN_APPROVED:
                    row.status, row.plan_decision_id = "active", UUID(p["decision_id"])
                else:
                    row.status = "delivered" if k == F.PROJECT_DELIVERED else "cancelled"
                _touch(row, fact)
        elif k == F.TASK_CREATED:
            db.execute(
                insert(TaskRow)
                .values(
                    task_id=UUID(p["task_id"]),
                    company_id=fact.company_id,
                    project_id=UUID(p["project_id"]),
                    title=p["title"],
                    description=p["description"],
                    acceptance_criteria=p.get("acceptance_criteria") or [],
                    kind=p.get("kind", "build"),
                    role=p.get("role"),
                    status="todo",
                    assignee=None,
                    depends_on=p.get("depends_on") or [],
                    inputs=p.get("inputs") or [],
                    rework_count=0,
                    reviews=[],
                    created_at=fact.created_at,
                    updated_at=fact.created_at,
                    last_seq=fact.seq,
                )
                .on_conflict_do_nothing(index_elements=["task_id"])
            )
        elif k == F.ARTIFACT_PRODUCED:
            db.execute(
                insert(ArtifactRow)
                .values(
                    artifact_id=UUID(p["artifact_id"]),
                    company_id=fact.company_id,
                    project_id=UUID(p["project_id"]),
                    task_id=UUID(p["task_id"]),
                    run_id=UUID(p["run_id"]) if p.get("run_id") else None,
                    path=p["path"],
                    blob_ref=p["blob_ref"],
                    size=p["size"],
                    media_type=p.get("media_type", "text/plain"),
                    uses=[],
                    created_at=fact.created_at,
                    last_seq=fact.seq,
                )
                .on_conflict_do_nothing(index_elements=["artifact_id"])
            )
        elif k == F.ARTIFACT_CONSUMED:
            row = db.get(ArtifactRow, UUID(p["artifact_id"]))
            if _fresh(row, fact):
                row.uses = [
                    *row.uses,
                    {
                        "task_id": p["task_id"],
                        "run_id": p.get("run_id"),
                        "at": fact.created_at.isoformat(),
                    },
                ]
                row.last_seq = fact.seq
        elif k == F.RUN_REQUESTED:
            return  # transient; the started fact is what the read model shows
        else:
            row = db.get(TaskRow, UUID(p["task_id"]))
            if _fresh(row, fact):
                _apply_task(row, fact)

    def reset(self, db: Session) -> None:
        db.execute(text(f"TRUNCATE {', '.join(TABLES)}"))


def _fresh(row, fact: Fact) -> bool:
    return row is not None and row.last_seq < fact.seq


def _touch(row, fact: Fact) -> None:
    row.updated_at = fact.created_at
    row.last_seq = fact.seq


def _apply_task(row: TaskRow, fact: Fact) -> None:
    p, k = fact.payload, fact.kind
    if k == F.TASK_ASSIGNED:
        row.assignee = p["assignee"]
    elif k == F.TASK_STARTED:
        row.status, row.current_run_id = "in_progress", UUID(p["run_id"])
    elif k == F.TASK_SUBMITTED:
        row.status = "in_review"
    elif k == F.TASK_REVIEWED:
        row.reviews = [
            *row.reviews,
            {
                "decision_id": p["decision_id"],
                "verdict": p["verdict"],
                "reviewer": p["reviewer"],
                "reason": p.get("reason"),
            },
        ]
        row.status = F.VERDICT_STATUS[p["verdict"]]
        if p["verdict"] == "rework":
            row.rework_count += 1
        if p["verdict"] in ("rework", "pass"):
            row.current_run_id = None
    elif k == F.TASK_BLOCKED:
        row.status = "blocked"
    elif k == F.TASK_UNBLOCKED:
        row.status = "todo"
    elif k == F.TASK_FAILED:
        row.status, row.current_run_id = "failed", None
    elif k == F.TASK_REOPENED:
        row.status = "todo"
    elif k == F.TASK_CANCELLED:
        row.status, row.current_run_id = "cancelled", None
    _touch(row, fact)

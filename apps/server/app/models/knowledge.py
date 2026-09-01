"""Knowledge/learning domain. See docs/architecture.md §3.2."""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.models.enums import (
    KnowledgeScope,
    KnowledgeStatus,
    LearningKind,
    SkillValidationStatus,
)


class MemoryEntry(TimestampMixin, Base):
    __tablename__ = "memory_entries"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    kind: Mapped[str] = mapped_column(String(50), default="note")  # note | observation | summary
    content: Mapped[str] = mapped_column(Text, default="")
    source_ref: Mapped[str] = mapped_column(String(500), default="")


class KnowledgeItem(TimestampMixin, Base):
    __tablename__ = "knowledge_items"

    scope: Mapped[str] = mapped_column(String(50), default=KnowledgeScope.private.value)
    owner_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, index=True
    )
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    content: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(200), default="", index=True)
    status: Mapped[str] = mapped_column(String(50), default=KnowledgeStatus.active.value)
    confidence: Mapped[float] = mapped_column(default=0.0)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    # Target scope recorded when a promotion proposal is submitted
    # (proposals have no dedicated table in the MVP).
    proposed_scope: Mapped[str | None] = mapped_column(String(50), nullable=True)


class Skill(TimestampMixin, Base):
    __tablename__ = "skills"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(default=1)
    attempts: Mapped[int] = mapped_column(default=0)
    success_count: Mapped[int] = mapped_column(default=0)
    avg_duration_sec: Mapped[float] = mapped_column(default=0.0)
    avg_cost: Mapped[float] = mapped_column(default=0.0)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    validation_status: Mapped[str] = mapped_column(
        String(50), default=SkillValidationStatus.candidate.value
    )


class LearningRecord(TimestampMixin, Base):
    __tablename__ = "learning_records"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(50), default=LearningKind.reflection.value)
    topic: Mapped[str] = mapped_column(String(200), default="")
    problem: Mapped[str] = mapped_column(Text, default="")
    observation: Mapped[str] = mapped_column(Text, default="")
    lesson: Mapped[str] = mapped_column(Text, default="")
    solution: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(default=0.0)
    sources: Mapped[list] = mapped_column(JSON, default=list)


class LearningPriority(TimestampMixin, Base):
    __tablename__ = "learning_priorities"
    __table_args__ = (UniqueConstraint("employee_id", "topic", name="uq_learning_priority"),)

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    score: Mapped[int] = mapped_column(default=0)  # 0-100
    reason: Mapped[str] = mapped_column(Text, default="")

"""Knowledge/learning domain. See docs/architecture.md §3.2."""

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
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
    # P5：Skill → Competency 的显式映射（可选）。SkillUsage 被确认有用时，可以
    # 为这条 competency 产生 Evidence —— Skill ≠ Competency，只是 Skill 的真实使用
    # 可以作为 Competency 的证据来源（docs/competency-system.md §6）。
    # 刻意不加 FK（与 employments.position_slot_id 同一纪律，见 v15 迁移 docstring）。
    competency_definition_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


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


class SkillUsage(TimestampMixin, Base):
    """候选技能的基准数据（§10.1）。

    两条腿各自独立、永不互相推导：`success` 是**客观事实**（来自 `_finalize`），
    `outcome` 是**人的判断**（useful / not_useful / 未评）。所以下游可以算
    “这个员工自认为管用的候选技能，事后真跑成了几次”，但反过来绝不成立：
    任务成功**不会**自动把 outcome 标成 useful（§10.2）。
    """

    __tablename__ = "skill_usages"
    __table_args__ = (UniqueConstraint("task_id", "skill_id", name="uq_skill_usage"),)

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    skill_id: Mapped[int] = mapped_column(ForeignKey("skills.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)
    work_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_sessions.id"), nullable=True
    )
    # 当时是 candidate 还是已 validated（影响归因分组，不改变事实）
    skill_validation_status: Mapped[str] = mapped_column(
        String(50), default=SkillValidationStatus.candidate.value
    )
    # 策略当时为什么把它交出去（审计用）：include_candidate_skills / matched_topk / ...
    selection_reason: Mapped[str] = mapped_column(String(100), default="")
    policy_version: Mapped[str] = mapped_column(String(50), default="")
    profile_revision: Mapped[int] = mapped_column(default=0)
    success: Mapped[bool] = mapped_column(default=False)
    # 人的判断；None = 尚未评价（pending）。不参与 success 计算。
    outcome: Mapped[str | None] = mapped_column(String(20), nullable=True)
    outcome_source: Mapped[str | None] = mapped_column(String(20), nullable=True)


class LearningPriority(TimestampMixin, Base):
    __tablename__ = "learning_priorities"
    __table_args__ = (UniqueConstraint("employee_id", "topic", name="uq_learning_priority"),)

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    score: Mapped[int] = mapped_column(default=0)  # 0-100
    reason: Mapped[str] = mapped_column(Text, default="")
    # §13.1：区分失败驱动（分数必须 ≥ FAILURE_PRIORITY_SCORE）与行为策略延伸项（必须 < 它）。
    source: Mapped[str] = mapped_column(String(50), default="")  # "" | failure | behavior-extension

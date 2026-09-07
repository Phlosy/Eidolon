"""Career & Talent Development（P10，docs/career-development.md）。

五张新表，全部是"职业侧"数据：

- `career_paths` / `career_path_steps`：职位间**推荐发展关系**（分支允许）；不强制流程
  （任意调岗仍可进行，只分析差距/风险/请求确认）。
- `career_events`：**职业履历审计**（Joined/Promoted/Transfered/…）—— 历史记录，
  **不是当前职位真相**（真相仍是 PositionAssignment）。
- `development_plans` / `development_plan_items`：发展计划（DRAFT→ACTIVE→…），
  只能给建议，不能直接改 EmployeeCompetency；Action 由 Human 触发。

铁律（都有测试）：
- CareerEvent 不能决定 current position；
- Position Fit / Career Readiness 是派生 read model，不落库；
- 能力仍只能经 Evidence→Assessment 变化；
- 任何 promote/transfer/acting 由 Human 显式确认并复用 PositionAssignment 工作流。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class CareerPath(TimestampMixin, Base):
    """职业发展路径（模板或公司自定义）。company_id NULL = 系统模板（只读）。"""

    __tablename__ = "career_paths"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_career_path_company_code"),)

    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    built_in: Mapped[bool] = mapped_column(Boolean, default=True)


class CareerPathStep(TimestampMixin, Base):
    """路径上 from→to 的一步（PROMOTION/LATERAL/SPECIALIZATION/MANAGEMENT/CROSS_FUNCTIONAL）。
    允许一对多分支；recommended 只是"被认可的发展"，不阻止其它任职。"""

    __tablename__ = "career_path_steps"
    __table_args__ = (
        UniqueConstraint(
            "career_path_id",
            "from_position_definition_id",
            "to_position_definition_id",
            name="uq_career_path_step",
        ),
    )

    career_path_id: Mapped[int] = mapped_column(ForeignKey("career_paths.id"), index=True)
    from_position_definition_id: Mapped[int] = mapped_column(
        ForeignKey("position_definitions.id"), index=True
    )
    to_position_definition_id: Mapped[int] = mapped_column(
        ForeignKey("position_definitions.id"), index=True
    )
    transition_type: Mapped[str] = mapped_column(String(30), default="promotion")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    minimum_tenure_days: Mapped[int] = mapped_column(Integer, default=0)
    recommended: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class CareerEvent(TimestampMixin, Base):
    """职业履历审计事件。**历史记录** —— 当前职位永远由 PositionAssignment 决定。"""

    __tablename__ = "career_events"
    __table_args__ = (Index("ix_career_events_employee_time", "employee_id", "effective_at"),)

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(40))
    position_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("position_definitions.id"), nullable=True
    )
    position_slot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class DevelopmentPlan(TimestampMixin, Base):
    """一个员工（可选目标职位）的发展计划。计划只生成建议，动作由 Human 触发。"""

    __tablename__ = "development_plans"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    target_position_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("position_definitions.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="draft"
    )  # draft | active | paused | completed | cancelled
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    source_fit_hash: Mapped[str] = mapped_column(String(64), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    target_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DevelopmentPlanItem(TimestampMixin, Base):
    """计划里的一个发展项（针对一个能力维度）。进度由 Reconciler 根据真实
    Evidence/Assessment/Competency 派生，不靠手点。"""

    __tablename__ = "development_plan_items"
    __table_args__ = (
        UniqueConstraint("plan_id", "competency_definition_id", name="uq_plan_item_competency"),
    )

    plan_id: Mapped[int] = mapped_column(ForeignKey("development_plans.id"), index=True)
    competency_definition_id: Mapped[int] = mapped_column(
        ForeignKey("competency_definitions.id"), index=True
    )
    need_type: Mapped[str] = mapped_column(String(30), default="competency_gap")
    objective: Mapped[str] = mapped_column(String(500), default="")
    target_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_confidence: Mapped[float | None] = mapped_column(nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="planned")
    recommended_actions: Mapped[list] = mapped_column(JSON, default=list)
    evidence_requirements: Mapped[list] = mapped_column(JSON, default=list)
    progress_metadata: Mapped[dict] = mapped_column(JSON, default=dict)

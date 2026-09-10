"""LearningSession（P11）—— 员工自主学习的正式领域对象。

WorkSession = 为公司项目工作；LearningSession = 为员工自身成长学习。两者分开。
学习产出（KnowledgeItem / Skill candidate / OpenQuestion / LearningPriority）写入
既有表（private 默认），并带 `learning_session_id` 元数据；学习不直接改 Competency。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class LearningSession(TimestampMixin, Base):
    __tablename__ = "learning_sessions"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    # deprecated（R1.1）：读口径已切到 person_id；列保留作兼容镜像，随表留存不删。
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    person_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    topic: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(Text, default="")
    source_type: Mapped[str] = mapped_column(String(40), default="manual")
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="planned")
    learning_mode: Mapped[str] = mapped_column(String(30), default="web_research")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    budget_tokens: Mapped[int] = mapped_column(Integer, default=0)
    budget_cost: Mapped[float] = mapped_column(Float, default=0.0)
    budget_minutes: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost_used: Mapped[float] = mapped_column(Float, default=0.0)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runtime_instance_id: Mapped[int | None] = mapped_column(
        ForeignKey("runtime_instances.id"), nullable=True
    )
    runtime_type: Mapped[str] = mapped_column(String(50), default="")
    provider_name: Mapped[str] = mapped_column(String(200), default="")
    model_name: Mapped[str] = mapped_column(String(200), default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    # 幂等 key：同一员工 + 来源 + topic 不得重复并行（唯一约束兜底）
    __table_args__ = (
        Index("ix_learning_sessions_employee_time", "employee_id", "started_at"),
        Index(
            "uq_learning_session_dupe",
            "employee_id",
            "source_type",
            "source_id",
            "topic",
            unique=True,
            sqlite_where=__import__("sqlalchemy").text(
                "status IN ('planned','running','waiting_budget')"
            ),
            postgresql_where=__import__("sqlalchemy").text(
                "status IN ('planned','running','waiting_budget')"
            ),
        ),
    )

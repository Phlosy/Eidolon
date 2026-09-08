"""v19.learning_autonomy —— 自主学习与行为策略快照（P11）

新增：
- `learning_sessions`（LearningSession 域对象：budget/usage/runtime/provider/model/
  状态机 planned→waiting_budget→running→completed/failed/cancelled；部分唯一索引
  防同一员工+来源+topic 重复并行）
- `work_sessions.behavior_snapshot_json` / `behavior_snapshot_hash`（行为策略快照）
- `knowledge_items.freshness_status` / `learned_at` / `last_validated_at`
  （Learned ≠ Truth；stale 检索可降置信）

学习不直接改 Competency（产出走既有 Evidence/Assessment 链）；budget 原子扣减在公司
settings.learning_usage（服务层事务）。Verify: up/down/up + alembic check。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r4a6b8c0d2e4"
down_revision: Union[str, Sequence[str], None] = "q3f5a7b9c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "learning_sessions",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("topic", sa.String(length=500), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("learning_mode", sa.String(length=30), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("budget_tokens", sa.Integer(), nullable=False),
        sa.Column("budget_cost", sa.Float(), nullable=False),
        sa.Column("budget_minutes", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("cost_used", sa.Float(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "runtime_instance_id",
            sa.Integer(),
            sa.ForeignKey("runtime_instances.id"),
            nullable=True,
        ),
        sa.Column("runtime_type", sa.String(length=50), nullable=False),
        sa.Column("provider_name", sa.String(length=200), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_learning_sessions_company_id"), "learning_sessions", ["company_id"])
    op.create_index(op.f("ix_learning_sessions_employee_id"), "learning_sessions", ["employee_id"])
    op.create_index(
        "ix_learning_sessions_employee_time", "learning_sessions", ["employee_id", "started_at"]
    )
    op.create_index(
        "uq_learning_session_dupe",
        "learning_sessions",
        ["employee_id", "source_type", "source_id", "topic"],
        unique=True,
        sqlite_where=sa.text("status IN ('planned','running','waiting_budget')"),
        postgresql_where=sa.text("status IN ('planned','running','waiting_budget')"),
    )
    op.add_column(
        "work_sessions",
        sa.Column("behavior_snapshot_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "work_sessions",
        sa.Column(
            "behavior_snapshot_hash",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.add_column(
        "knowledge_items",
        sa.Column(
            "freshness_status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'fresh'"),
        ),
    )
    op.add_column("knowledge_items", sa.Column("learned_at", sa.DateTime(), nullable=True))
    op.add_column("knowledge_items", sa.Column("last_validated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("knowledge_items", "last_validated_at")
    op.drop_column("knowledge_items", "learned_at")
    op.drop_column("knowledge_items", "freshness_status")
    op.drop_column("work_sessions", "behavior_snapshot_hash")
    op.drop_column("work_sessions", "behavior_snapshot_json")
    op.drop_index("uq_learning_session_dupe", table_name="learning_sessions")
    op.drop_index("ix_learning_sessions_employee_time", table_name="learning_sessions")
    op.drop_index(op.f("ix_learning_sessions_employee_id"), table_name="learning_sessions")
    op.drop_index(op.f("ix_learning_sessions_company_id"), table_name="learning_sessions")
    op.drop_table("learning_sessions")

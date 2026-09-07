"""v18.career_development —— 职业发展与人才培养（P10）

新增表（五张）：
- `career_paths` / `career_path_steps`：职位间推荐发展关系（分支；company_id NULL=系统模板）
- `career_events`：职业履历审计（Joined/Promoted/Transferred/…）—— 历史记录，
  当前任职仍由 PositionAssignment 决定（不复制）
- `development_plans` / `development_plan_items`：发展计划（只能给建议，不改能力）

不加第二套任职真相：不新建 employments 的替代表；CareerEvent.position_slot_id 不带 FK
（历史引用，SQLite 无 ALTER CONSTRAINT；完整性由服务层）。

Verify: upgrade head → downgrade -1 → upgrade head；alembic check 无漂移。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q3f5a7b9c1d2"
down_revision: Union[str, Sequence[str], None] = "p2e4a6c8d0f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "career_paths",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("built_in", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "code", name="uq_career_path_company_code"),
    )
    op.create_table(
        "career_path_steps",
        sa.Column("career_path_id", sa.Integer(), sa.ForeignKey("career_paths.id"), nullable=False),
        sa.Column(
            "from_position_definition_id",
            sa.Integer(),
            sa.ForeignKey("position_definitions.id"),
            nullable=False,
        ),
        sa.Column(
            "to_position_definition_id",
            sa.Integer(),
            sa.ForeignKey("position_definitions.id"),
            nullable=False,
        ),
        sa.Column("transition_type", sa.String(length=30), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("minimum_tenure_days", sa.Integer(), nullable=False),
        sa.Column("recommended", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "career_path_id",
            "from_position_definition_id",
            "to_position_definition_id",
            name="uq_career_path_step",
        ),
    )
    op.create_index(
        op.f("ix_career_path_steps_career_path_id"), "career_path_steps", ["career_path_id"]
    )
    op.create_index(
        op.f("ix_career_path_steps_from_position_definition_id"),
        "career_path_steps",
        ["from_position_definition_id"],
    )
    op.create_index(
        op.f("ix_career_path_steps_to_position_definition_id"),
        "career_path_steps",
        ["to_position_definition_id"],
    )
    op.create_table(
        "career_events",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column(
            "position_definition_id",
            sa.Integer(),
            sa.ForeignKey("position_definitions.id"),
            nullable=True,
        ),
        sa.Column("position_slot_id", sa.Integer(), nullable=True),  # 历史引用，无 FK
        sa.Column("from_position_id", sa.Integer(), nullable=True),
        sa.Column("to_position_id", sa.Integer(), nullable=True),
        sa.Column("effective_at", sa.DateTime(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_career_events_company_id"), "career_events", ["company_id"])
    op.create_index(op.f("ix_career_events_employee_id"), "career_events", ["employee_id"])
    op.create_index(
        "ix_career_events_employee_time", "career_events", ["employee_id", "effective_at"]
    )
    op.create_table(
        "development_plans",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column(
            "target_position_definition_id",
            sa.Integer(),
            sa.ForeignKey("position_definitions.id"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("source_fit_hash", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("target_date", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_development_plans_company_id"), "development_plans", ["company_id"])
    op.create_index(op.f("ix_development_plans_employee_id"), "development_plans", ["employee_id"])
    op.create_table(
        "development_plan_items",
        sa.Column("plan_id", sa.Integer(), sa.ForeignKey("development_plans.id"), nullable=False),
        sa.Column(
            "competency_definition_id",
            sa.Integer(),
            sa.ForeignKey("competency_definitions.id"),
            nullable=False,
        ),
        sa.Column("need_type", sa.String(length=30), nullable=False),
        sa.Column("objective", sa.String(length=500), nullable=False),
        sa.Column("target_score", sa.Integer(), nullable=True),
        sa.Column("target_confidence", sa.Float(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("recommended_actions", sa.JSON(), nullable=False),
        sa.Column("evidence_requirements", sa.JSON(), nullable=False),
        sa.Column("progress_metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "competency_definition_id", name="uq_plan_item_competency"),
    )
    op.create_index(
        op.f("ix_development_plan_items_plan_id"), "development_plan_items", ["plan_id"]
    )
    op.create_index(
        op.f("ix_development_plan_items_competency_definition_id"),
        "development_plan_items",
        ["competency_definition_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_development_plan_items_competency_definition_id"),
        table_name="development_plan_items",
    )
    op.drop_index(op.f("ix_development_plan_items_plan_id"), table_name="development_plan_items")
    op.drop_table("development_plan_items")
    op.drop_index(op.f("ix_development_plans_employee_id"), table_name="development_plans")
    op.drop_index(op.f("ix_development_plans_company_id"), table_name="development_plans")
    op.drop_table("development_plans")
    op.drop_index("ix_career_events_employee_time", table_name="career_events")
    op.drop_index(op.f("ix_career_events_employee_id"), table_name="career_events")
    op.drop_index(op.f("ix_career_events_company_id"), table_name="career_events")
    op.drop_table("career_events")
    op.drop_index(
        op.f("ix_career_path_steps_to_position_definition_id"),
        table_name="career_path_steps",
    )
    op.drop_index(
        op.f("ix_career_path_steps_from_position_definition_id"),
        table_name="career_path_steps",
    )
    op.drop_index(op.f("ix_career_path_steps_career_path_id"), table_name="career_path_steps")
    op.drop_table("career_path_steps")
    op.drop_table("career_paths")

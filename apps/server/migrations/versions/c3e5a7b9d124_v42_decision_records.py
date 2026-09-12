"""v42 decision records & tool audits (M2.4)

Revision ID: c3e5a7b9d124
Revises: b2d4f6a8c013
Create Date: 2026-09-11 23:30:00.000000

M2.4（docs/m2-implementation-plan.md §7）—— **Decision Envelope** 的两张表。

用户拍板：`DecisionRecord`（意图）/ `ToolAudit`（执行事实）/ Domain State（真相）三层不混。

| 表 | 角色 |
| --- | --- |
| `decision_records` | 管理 Agent **为什么**做出这个决定（管理语义 + 有界上下文 + 状态）|
| `tool_audits` | 为执行这个决定，系统**实际执行了什么**（入参 / 授权结果 / 出参 / 错误）|

三条设计裁决写在迁移里：

1. **关联只有一个方向**：`tool_audits.decision_id → decision_records.id`（部分允许 NULL ——
   不属于任何决策的独立工具调用是合法形态）。**没有** `decision_records.audit_ids[]`。
2. **`decision_records` 不复制执行细节**：tool 名 / 入参 / 出参 / 错误全在 `tool_audits`；
   动作计数是派生量，读时反查，不落列（仓库 ADR-12）。
3. **不做数据回填**：M2.3 把工具审计写在 `audit_logs.after_json` 里（复用既有表的最小改动）。
   那些行是**历史事实**，原样留在 `audit_logs` 里；本迁移**不搬运**它们 ——
   把 JSON blob 拆成结构化行需要猜测字段语义，而"宁缺不错"是本仓库的既有纪律。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c3e5a7b9d124"
down_revision = "b2d4f6a8c013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "decision_records",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("actor_employee_id", sa.Integer(), nullable=False),
        sa.Column("actor_person_id", sa.Integer(), nullable=True),
        sa.Column("acting_position_assignment_id", sa.Integer(), nullable=True),
        sa.Column("acting_position_definition_id", sa.Integer(), nullable=True),
        sa.Column("acting_position_code", sa.String(length=100), nullable=True),
        sa.Column("scope", sa.String(length=80), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("decision_type", sa.String(length=40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("intended_outcome", sa.Text(), nullable=False),
        sa.Column("context_json", sa.JSON(), nullable=False),
        sa.Column("context_hash", sa.String(length=64), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("authority_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("outcome_note", sa.Text(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("parent_decision_id", sa.Integer(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["actor_employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_decision_records_company_id", "decision_records", ["company_id"])
    op.create_index("ix_decision_records_actor_employee_id", "decision_records", ["actor_employee_id"])
    op.create_index("ix_decision_records_actor_person_id", "decision_records", ["actor_person_id"])
    op.create_index("ix_decision_records_project_id", "decision_records", ["project_id"])
    op.create_index("ix_decision_records_task_id", "decision_records", ["task_id"])
    op.create_index("ix_decision_records_decision_type", "decision_records", ["decision_type"])
    op.create_index("ix_decision_records_status", "decision_records", ["status"])
    op.create_index("ix_decision_records_company_created", "decision_records", ["company_id", "id"])
    op.create_index("ix_decision_records_scope", "decision_records", ["scope"])
    op.create_index("ix_decision_records_parent", "decision_records", ["parent_decision_id"])

    op.create_table(
        "tool_audits",
        sa.Column("tool_name", sa.String(length=80), nullable=False),
        sa.Column("decision_id", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("side_effect", sa.String(length=16), nullable=False, server_default=sa.text("'read'")),
        sa.Column(
            "decision_semantics",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
        sa.Column("autonomy", sa.String(length=24), nullable=False),
        sa.Column("transport", sa.String(length=16), nullable=False),
        sa.Column("actor_employee_id", sa.Integer(), nullable=True),
        sa.Column("actor_person_id", sa.Integer(), nullable=True),
        sa.Column("actor_company_id", sa.Integer(), nullable=True),
        sa.Column("origin", sa.String(length=24), nullable=False),
        sa.Column("work_session_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("arguments_json", sa.JSON(), nullable=False),
        sa.Column("arguments_digest", sa.String(length=64), nullable=False),
        sa.Column("authority_json", sa.JSON(), nullable=True),
        sa.Column("authority_allowed", sa.Boolean(), nullable=True),
        sa.Column("authority_reason", sa.String(length=64), nullable=False),
        sa.Column("authority_grant_ids", sa.JSON(), nullable=False),
        sa.Column("authority_grants_hash", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_audits_tool_name", "tool_audits", ["tool_name"])
    op.create_index("ix_tool_audits_decision_id", "tool_audits", ["decision_id"])
    op.create_index("ix_tool_audits_outcome", "tool_audits", ["outcome"])
    op.create_index("ix_tool_audits_actor_employee_id", "tool_audits", ["actor_employee_id"])
    op.create_index("ix_tool_audits_actor_company_id", "tool_audits", ["actor_company_id"])
    op.create_index("ix_tool_audits_decision", "tool_audits", ["decision_id", "id"])
    op.create_index("ix_tool_audits_tool_created", "tool_audits", ["tool_name", "id"])
    op.create_index(
        "ix_tool_audits_company_created", "tool_audits", ["actor_company_id", "id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    for index_name in (
        "ix_tool_audits_company_created",
        "ix_tool_audits_tool_created",
        "ix_tool_audits_decision",
        "ix_tool_audits_actor_company_id",
        "ix_tool_audits_actor_employee_id",
        "ix_tool_audits_outcome",
        "ix_tool_audits_decision_id",
        "ix_tool_audits_tool_name",
    ):
        op.drop_index(index_name, table_name="tool_audits")
    op.drop_table("tool_audits")

    for index_name in (
        "ix_decision_records_parent",
        "ix_decision_records_scope",
        "ix_decision_records_company_created",
        "ix_decision_records_status",
        "ix_decision_records_decision_type",
        "ix_decision_records_task_id",
        "ix_decision_records_project_id",
        "ix_decision_records_actor_person_id",
        "ix_decision_records_actor_employee_id",
        "ix_decision_records_company_id",
    ):
        op.drop_index(index_name, table_name="decision_records")
    op.drop_table("decision_records")

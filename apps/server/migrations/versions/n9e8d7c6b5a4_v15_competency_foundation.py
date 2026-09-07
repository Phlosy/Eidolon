"""v15.competency_foundation —— P5 人才能力数据基座

docs/competency-system.md / docs/assessment-system.md 的 P5 落地。**只做加法**：

1. 新表（六张）：
   - `competency_domains`（能力域；company_id NULL = 全局内置目录，非空 = 公司自定义）
   - `competency_definitions`（能力定义；domain 下 code 唯一）
   - `assessment_runs`（考核/重算审计锚点 —— P10 会扩展 profile/criteria/results，
     本表先行落地确定性重算所需骨架；`inputs_hash` 是可重放硬保证）
   - `employee_competencies`（员工能力；score/confidence 只能由聚合器写入，
     score NULL = 未评。**不预建行**：无证据的维度由查询呈现 unrated）
   - `competency_evidence`（能力证据；source_kind/source_ref 可反查）
   - `position_competency_requirements`（职位能力要求 —— schema 先行，P8/P10 才做正式匹配）
2. `skills` 加一列：`competency_definition_id`（Skill → Competency 的显式映射，可选）。

刻意**不加**外键的一处：`skills.competency_definition_id`。本项目 SQLite 从不
`PRAGMA foreign_keys=ON`，FK 声明不参与约束；而给历史表 `skills` 加 FK 需要 ALTER，
SQLite 不支持（要么 batch 重建整表）。语义由写路径校验保证 —— 与
`employments.position_slot_id` 同一纪律（position-system.md §6.1）。

本迁移不回填任何数据：目录种子由启动 seed 幂等写入（services/seed.py），
新表先空着不破坏任何既有行为。

Verify: `upgrade head` → `downgrade -1` → `upgrade head`；`alembic check` 无漂移。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n9e8d7c6b5a4"
down_revision: str | Sequence[str] | None = "m8b1d4e7f063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "competency_domains",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("competency_domains.id"), nullable=True),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("built_in", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "code", name="uq_competency_domain_company_code"),
    )
    op.create_table(
        "competency_definitions",
        sa.Column(
            "domain_id", sa.Integer(), sa.ForeignKey("competency_domains.id"), nullable=False
        ),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("facets", sa.JSON(), nullable=False),
        sa.Column("evidence_kinds", sa.JSON(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("built_in", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain_id", "code", name="uq_competency_definition_domain_code"),
    )
    op.create_index(
        op.f("ix_competency_definitions_domain_id"), "competency_definitions", ["domain_id"]
    )
    # assessment_runs 先建：competency_evidence.assessment_run_id 引它
    op.create_table(
        "assessment_runs",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=True),  # P10 接 profile 表
        sa.Column("position_assignment_id", sa.Integer(), nullable=True),
        sa.Column("triggered_by", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("window_from", sa.DateTime(), nullable=True),
        sa.Column("window_to", sa.DateTime(), nullable=True),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=50), nullable=False),
        sa.Column("inputs_hash", sa.String(length=64), nullable=False),
        sa.Column("outputs", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assessment_runs_company_id"), "assessment_runs", ["company_id"])
    op.create_index(op.f("ix_assessment_runs_employee_id"), "assessment_runs", ["employee_id"])
    op.create_index(op.f("ix_assessment_runs_inputs_hash"), "assessment_runs", ["inputs_hash"])
    op.create_table(
        "employee_competencies",
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column(
            "competency_definition_id",
            sa.Integer(),
            sa.ForeignKey("competency_definitions.id"),
            nullable=False,
        ),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("last_assessed_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("trend", sa.Integer(), nullable=True),
        sa.Column("trend_window", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "employee_id", "competency_definition_id", name="uq_employee_competency"
        ),
    )
    op.create_index(
        op.f("ix_employee_competencies_competency_definition_id"),
        "employee_competencies",
        ["competency_definition_id"],
    )
    op.create_index(
        op.f("ix_employee_competencies_employee_id"), "employee_competencies", ["employee_id"]
    )
    op.create_table(
        "position_competency_requirements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "position_definition_id",
            sa.Integer(),
            sa.ForeignKey("position_definitions.id"),
            nullable=False,
        ),
        sa.Column(
            "competency_definition_id",
            sa.Integer(),
            sa.ForeignKey("competency_definitions.id"),
            nullable=False,
        ),
        sa.Column("minimum_score", sa.Integer(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "position_definition_id",
            "competency_definition_id",
            name="uq_position_competency_requirement",
        ),
    )
    op.create_index(
        op.f("ix_position_competency_requirements_competency_definition_id"),
        "position_competency_requirements",
        ["competency_definition_id"],
    )
    op.create_index(
        op.f("ix_position_competency_requirements_position_definition_id"),
        "position_competency_requirements",
        ["position_definition_id"],
    )
    op.create_table(
        "competency_evidence",
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column(
            "competency_definition_id",
            sa.Integer(),
            sa.ForeignKey("competency_definitions.id"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("source_ref", sa.String(length=500), nullable=False),
        sa.Column(
            "assessment_run_id",
            sa.Integer(),
            sa.ForeignKey("assessment_runs.id"),
            nullable=True,
        ),
        sa.Column("signal", sa.Integer(), nullable=True),
        sa.Column("quality", sa.Float(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_competency_evidence_assessment_run_id"),
        "competency_evidence",
        ["assessment_run_id"],
    )
    op.create_index(
        op.f("ix_competency_evidence_competency_definition_id"),
        "competency_evidence",
        ["competency_definition_id"],
    )
    op.create_index(
        op.f("ix_competency_evidence_employee_id"), "competency_evidence", ["employee_id"]
    )
    # skills 加列：FK 不加（SQLite 无法 ALTER 加 FK；语义由写路径保证，见模块 docstring）
    op.add_column(
        "skills",
        sa.Column("competency_definition_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        op.f("ix_skills_competency_definition_id"), "skills", ["competency_definition_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_skills_competency_definition_id"), table_name="skills")
    op.drop_column("skills", "competency_definition_id")
    op.drop_index(op.f("ix_competency_evidence_employee_id"), table_name="competency_evidence")
    op.drop_index(
        op.f("ix_competency_evidence_competency_definition_id"), table_name="competency_evidence"
    )
    op.drop_index(
        op.f("ix_competency_evidence_assessment_run_id"), table_name="competency_evidence"
    )
    op.drop_table("competency_evidence")
    op.drop_index(
        op.f("ix_position_competency_requirements_position_definition_id"),
        table_name="position_competency_requirements",
    )
    op.drop_index(
        op.f("ix_position_competency_requirements_competency_definition_id"),
        table_name="position_competency_requirements",
    )
    op.drop_table("position_competency_requirements")
    op.drop_index(op.f("ix_employee_competencies_employee_id"), table_name="employee_competencies")
    op.drop_index(
        op.f("ix_employee_competencies_competency_definition_id"),
        table_name="employee_competencies",
    )
    op.drop_table("employee_competencies")
    op.drop_index(op.f("ix_assessment_runs_inputs_hash"), table_name="assessment_runs")
    op.drop_index(op.f("ix_assessment_runs_employee_id"), table_name="assessment_runs")
    op.drop_index(op.f("ix_assessment_runs_company_id"), table_name="assessment_runs")
    op.drop_table("assessment_runs")
    op.drop_index(op.f("ix_competency_definitions_domain_id"), table_name="competency_definitions")
    op.drop_table("competency_definitions")
    op.drop_table("competency_domains")

"""v16.assessment_evidence_pipeline —— P6 考核与证据流水线

docs/assessment-system.md（四表 + 链）的正式落地 + docs/evidence-pipeline.md。

新增表（六张）：
- `assessment_profiles`：考核档案，`uq(code, version)` —— 模板版本化（§十）
- `assessment_criteria`：考核维度（可贡献给多个能力）
- `assessment_criterion_competencies`：criterion → competency 中间表（contribution_weight）
- `assessment_results`：一次 run 的逐条观测/贡献（解释“为什么是 72”）
- `competency_expectations`：工作项/职位模板期望验证哪些能力（PRIMARY/SUPPORTING/OPTIONAL）

改动既有表（**只加列，不重建、不动旧行**）：
- `assessment_runs`：加 `assessment_type` / `profile_version`（run 指向当时用的 profile 版本）
- `competency_evidence`：加 `strength` / `reliability`（0..1）与 `environment`（mock 打折）

不破坏 P5：Evidence / EmployeeCompetency / AssessmentRun / SkillUsage 均保留原语义，
新列全部可空或有 server_default。

Verify: `upgrade head` → `downgrade -1` → `upgrade head`；`alembic check` 无漂移。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "o1f2e3d4c5b6"
down_revision: str | Sequence[str] | None = "n9e8d7c6b5a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assessment_profiles",
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("applies_to_kind", sa.String(length=30), nullable=False),
        sa.Column(
            "position_definition_id",
            sa.Integer(),
            sa.ForeignKey("position_definitions.id"),
            nullable=True,
        ),
        sa.Column("min_evidence_count", sa.Integer(), nullable=False),
        sa.Column("half_life_days", sa.Float(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=50), nullable=False),
        sa.Column("built_in", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "version", name="uq_assessment_profile_code_version"),
    )
    op.create_table(
        "assessment_criteria",
        sa.Column(
            "profile_id", sa.Integer(), sa.ForeignKey("assessment_profiles.id"), nullable=False
        ),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("evidence_kinds", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "code", name="uq_assessment_criterion_code"),
    )
    op.create_index(
        op.f("ix_assessment_criteria_profile_id"), "assessment_criteria", ["profile_id"]
    )
    op.create_table(
        "assessment_criterion_competencies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "criterion_id", sa.Integer(), sa.ForeignKey("assessment_criteria.id"), nullable=False
        ),
        sa.Column(
            "competency_definition_id",
            sa.Integer(),
            sa.ForeignKey("competency_definitions.id"),
            nullable=False,
        ),
        sa.Column("contribution_weight", sa.Float(), nullable=False),
        sa.Column("evidence_type", sa.String(length=30), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "criterion_id", "competency_definition_id", name="uq_criterion_competency"
        ),
    )
    op.create_index(
        op.f("ix_assessment_criterion_competencies_criterion_id"),
        "assessment_criterion_competencies",
        ["criterion_id"],
    )
    op.create_index(
        op.f("ix_assessment_criterion_competencies_competency_definition_id"),
        "assessment_criterion_competencies",
        ["competency_definition_id"],
    )
    op.create_table(
        "assessment_results",
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("assessment_runs.id"), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column(
            "criterion_id", sa.Integer(), sa.ForeignKey("assessment_criteria.id"), nullable=True
        ),
        sa.Column(
            "competency_definition_id",
            sa.Integer(),
            sa.ForeignKey("competency_definitions.id"),
            nullable=True,
        ),
        sa.Column("observed_score", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("contribution", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_assessment_results_run_id"), "assessment_results", ["run_id"])
    op.create_index(
        op.f("ix_assessment_results_criterion_id"), "assessment_results", ["criterion_id"]
    )
    op.create_index(
        op.f("ix_assessment_results_competency_definition_id"),
        "assessment_results",
        ["competency_definition_id"],
    )
    op.create_table(
        "competency_expectations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scope_kind", sa.String(length=30), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column(
            "competency_definition_id",
            sa.Integer(),
            sa.ForeignKey("competency_definitions.id"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scope_kind",
            "scope_id",
            "competency_definition_id",
            name="uq_competency_expectation",
        ),
    )
    op.create_index(
        op.f("ix_competency_expectations_competency_definition_id"),
        "competency_expectations",
        ["competency_definition_id"],
    )
    # ---- 既有表加列（不重建，旧行保留） ----
    op.add_column(
        "assessment_runs",
        sa.Column(
            "assessment_type", sa.String(length=30), nullable=False, server_default=sa.text("''")
        ),
    )
    op.add_column(
        "assessment_runs",
        sa.Column("profile_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "competency_evidence",
        sa.Column("strength", sa.Float(), nullable=True),
    )
    op.add_column(
        "competency_evidence",
        sa.Column("reliability", sa.Float(), nullable=True),
    )
    op.add_column(
        "competency_evidence",
        sa.Column(
            "environment", sa.String(length=20), nullable=False, server_default=sa.text("''")
        ),
    )
    op.create_index(
        op.f("ix_competency_evidence_emp_comp_kind"),
        "competency_evidence",
        ["employee_id", "competency_definition_id", "source_kind"],
    )
    op.create_index(
        op.f("ix_competency_evidence_employee_occurred"),
        "competency_evidence",
        ["employee_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_competency_evidence_employee_occurred"), table_name="competency_evidence"
    )
    op.drop_index(op.f("ix_competency_evidence_emp_comp_kind"), table_name="competency_evidence")
    op.drop_column("competency_evidence", "environment")
    op.drop_column("competency_evidence", "reliability")
    op.drop_column("competency_evidence", "strength")
    op.drop_column("assessment_runs", "profile_version")
    op.drop_column("assessment_runs", "assessment_type")
    op.drop_index(
        op.f("ix_competency_expectations_competency_definition_id"),
        table_name="competency_expectations",
    )
    op.drop_table("competency_expectations")
    op.drop_index(
        op.f("ix_assessment_results_competency_definition_id"), table_name="assessment_results"
    )
    op.drop_index(op.f("ix_assessment_results_criterion_id"), table_name="assessment_results")
    op.drop_index(op.f("ix_assessment_results_run_id"), table_name="assessment_results")
    op.drop_table("assessment_results")
    op.drop_index(
        op.f("ix_assessment_criterion_competencies_competency_definition_id"),
        table_name="assessment_criterion_competencies",
    )
    op.drop_index(
        op.f("ix_assessment_criterion_competencies_criterion_id"),
        table_name="assessment_criterion_competencies",
    )
    op.drop_table("assessment_criterion_competencies")
    op.drop_index(op.f("ix_assessment_criteria_profile_id"), table_name="assessment_criteria")
    op.drop_table("assessment_criteria")
    op.drop_table("assessment_profiles")

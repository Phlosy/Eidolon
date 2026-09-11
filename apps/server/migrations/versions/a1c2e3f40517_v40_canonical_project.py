"""v40 canonical executable project (M2.1)

Revision ID: a1c2e3f40517
Revises: 64fec2d13d9b
Create Date: 2026-09-11 20:40:00.000000

M2.1（docs/m2-agent-implementation-plan.md §4）—— `projects` 成为唯一权威工作根。

**纯 additive**（B5：SQLite 只加列，不重建表、不加 FK）：

| 列 | 语义 |
| --- | --- |
| `work_mode` | 产品工作模式快照（`guided` \\| `managed`）；**NULL = M2.1 之前的历史项目** |
| `planning_fixture` | 规划 fixture（`none` \\| `deterministic_template`）；基础设施轴，不是产品模式 |
| `spec_version` | Canonical Spec 版本 |
| `work_intake_position_code` | Work Intake 路由目标职位 code 快照 |
| `management_employee_id` / `management_person_id` / `management_assigned_at` | 当前管理 actor 快照指针（非长期真相） |

**回填是事实驱动的，不是猜测**（宁缺不错）：

1. 有 `project_phases` 行 ⇒ 它就是 v0.5 结构化交付项目 ⇒ `work_mode='guided'`、
   `planning_fixture='none'`；
2. 没有 phase、但有模板执行图产生的任务 kind（order_review/planning/…/final_review）
   ⇒ 它当年跑的是固定模板 ⇒ `planning_fixture='deterministic_template'`；
   **`work_mode` 保持 NULL** —— 当年没有任何 Agent 做过规划决策，
   把它写成 `managed` 是伪造历史，写成 `guided` 是伪造流程。
3. 其余（既无 phase 也无模板任务）两边都留 NULL。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1c2e3f40517"
down_revision = "64fec2d13d9b"
branch_labels = None
depends_on = None

#: 固定模板执行图产生的任务 kind（app/workflow/orchestrator.py 的历史模板）
_TEMPLATE_TASK_KINDS = (
    "order_review",
    "planning",
    "research",
    "development",
    "testing",
    "final_review",
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("projects", sa.Column("work_mode", sa.String(length=20), nullable=True))
    op.add_column("projects", sa.Column("planning_fixture", sa.String(length=30), nullable=True))
    op.add_column(
        "projects",
        sa.Column("spec_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "projects", sa.Column("work_intake_position_code", sa.String(length=100), nullable=True)
    )
    op.add_column("projects", sa.Column("management_employee_id", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("management_person_id", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("management_assigned_at", sa.DateTime(), nullable=True))
    op.create_index("ix_projects_work_mode", "projects", ["work_mode"])

    # ---- 事实驱动回填（见模块 docstring 的三条规则）----
    kinds = ", ".join(f"'{kind}'" for kind in _TEMPLATE_TASK_KINDS)
    op.execute(
        sa.text(
            """
            UPDATE projects
               SET work_mode = 'guided',
                   planning_fixture = 'none'
             WHERE EXISTS (
                   SELECT 1 FROM project_phases WHERE project_phases.project_id = projects.id
             )
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            UPDATE projects
               SET planning_fixture = 'deterministic_template'
             WHERE work_mode IS NULL
               AND planning_fixture IS NULL
               AND NOT EXISTS (
                   SELECT 1 FROM project_phases WHERE project_phases.project_id = projects.id
               )
               AND EXISTS (
                   SELECT 1 FROM tasks
                    WHERE tasks.project_id = projects.id
                      AND tasks.kind IN ({kinds})
               )
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_projects_work_mode", table_name="projects")
    op.drop_column("projects", "management_assigned_at")
    op.drop_column("projects", "management_person_id")
    op.drop_column("projects", "management_employee_id")
    op.drop_column("projects", "work_intake_position_code")
    op.drop_column("projects", "spec_version")
    op.drop_column("projects", "planning_fixture")
    op.drop_column("projects", "work_mode")

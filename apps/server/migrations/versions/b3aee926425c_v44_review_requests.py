"""v44 review requests & facts (M2.7)

Revision ID: b3aee926425c
Revises: 43a70cd19cc7
Create Date: 2026-09-12 20:54:48.260463

M2.7（docs/m2-implementation-plan.md §10 / 设计 §12，W17 / RV1–RV8）
—— **Review / Rework / Replan**：worker 完成后不再自动 `done`。

**两张表刻意分开**（H6/RV3）：它们的**归属不同**。

| 表 | 谁写 | 内容 |
| --- | --- | --- |
| `review_facts` | **系统**（可复核） | artifact / inputs / session / 声明差异之类的事实，**没有**通过/不通过 |
| `review_requests` | **人/Agent**（Reviewer） | 谁请谁评、结论（verdict）、理由、决策链接 |

三条设计裁决写在迁移里：

1. **状态不复述结论**：`review_requests.status` 只有 `open` / `decided`，
   结论在 `verdict` 一列。把 `PASS` 塞进 status 会让同一事实有两个落点。
2. **结论追加式**（RV4）：写下即不可改写（改判走新请求）。`verdict_decision_id`
   指向承载该结论的 `decision_records` 行，**单向**（与 DR3 同款方向）。
3. **回填不猜**：`tasks.rework_count` 从 0 起算。历史任务**没有**可复核的返工记录
   （旧路径的 `request_rework` 只改状态、不留计数），所以不推测 —— 宁可少算，
   也不把一个编出来的数字写进事实列。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3aee926425c"
down_revision: str | Sequence[str] | None = "43a70cd19cc7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "review_requests",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_employee_id", sa.Integer(), nullable=False),
        #: 发起时**必须**指定评审人：系统不替管理层选人（W1/R2）
        sa.Column("reviewer_employee_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default=sa.text("'open'")),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=True),
        sa.Column("verdict_notes", sa.Text(), nullable=False),
        sa.Column("verdict_decision_id", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["requested_by_employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["reviewer_employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["verdict_decision_id"], ["decision_records.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_requests_company_id", "review_requests", ["company_id"])
    op.create_index("ix_review_requests_project_id", "review_requests", ["project_id"])
    op.create_index("ix_review_requests_task_id", "review_requests", ["task_id"])
    op.create_index(
        "ix_review_requests_reviewer_employee_id", "review_requests", ["reviewer_employee_id"]
    )
    op.create_index("ix_review_requests_status", "review_requests", ["status"])
    op.create_index("ix_review_requests_task_status", "review_requests", ["task_id", "status"])
    op.create_index(
        "ix_review_requests_reviewer_status", "review_requests", ["reviewer_employee_id", "status"]
    )

    op.create_table(
        "review_facts",
        sa.Column("review_request_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False, server_default=sa.text("'system'")),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["review_request_id"], ["review_requests.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_review_facts_review_request_id", "review_facts", ["review_request_id"])
    op.create_index("ix_review_facts_task_id", "review_facts", ["task_id"])
    op.create_index("ix_review_facts_request_kind", "review_facts", ["review_request_id", "kind"])

    # ---- 返工计数（RV5）：从 0 起算，不回填（历史无可复核记录，不猜）----
    op.add_column(
        "tasks",
        sa.Column("rework_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("tasks", "rework_count")
    op.drop_index("ix_review_facts_request_kind", table_name="review_facts")
    op.drop_index("ix_review_facts_task_id", table_name="review_facts")
    op.drop_index("ix_review_facts_review_request_id", table_name="review_facts")
    op.drop_table("review_facts")
    op.drop_index("ix_review_requests_reviewer_status", table_name="review_requests")
    op.drop_index("ix_review_requests_task_status", table_name="review_requests")
    op.drop_index("ix_review_requests_status", table_name="review_requests")
    op.drop_index("ix_review_requests_reviewer_employee_id", table_name="review_requests")
    op.drop_index("ix_review_requests_task_id", table_name="review_requests")
    op.drop_index("ix_review_requests_project_id", table_name="review_requests")
    op.drop_index("ix_review_requests_company_id", table_name="review_requests")
    op.drop_table("review_requests")

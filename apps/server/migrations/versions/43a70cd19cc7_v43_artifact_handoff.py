"""v43 artifact handoff (M2.6)

Revision ID: 43a70cd19cc7
Revises: c3e5a7b9d124
Create Date: 2026-09-12 17:17:38.529338

M2.6（docs/m2-implementation-plan.md §9 / 设计 §14c，W19 / H1–H8）
—— **Artifact Handoff & Shared Work Context**。

**不新建 Artifact 表**（H1/G4）：交付物的内容、版本、sha256 仍在
`drive_nodes` / `drive_revisions`。本次只补三类「工作域事实」：

| 变更 | 事实 | 纪律 |
| --- | --- | --- |
| `drive_nodes.task_id` | 产出归属：哪个 Task 的产物 | 产出时写入、此后不变；无关联为 NULL |
| `tasks.produces_json` | 声明的**预期**交付物类型 | 声明是计划，不是产出事实 |
| `task_inputs` | 「要用谁的产品」= 计划声明 | 指向 **Task** 而非 artifact：产物还没产生（H4） |
| `artifact_links` | 「被谁在哪次会话用掉了」= 使用事实 | **只记使用**：产出归属不在这里重复（H3） |

三条设计裁决写在迁移里：

1. **同一个事实不留两个落点**：产出归属 = `drive_nodes.task_id`（+ 既有的
   `work_session_id` 表达"哪次会话"）；`artifact_links` 只记**使用**，
   `role` 的值域刻意只有一个成员（`consumed_by`）。
2. **声明 ≠ 引用**（H4）：`task_inputs.source_task_id` 是**上游 Task**。
   产物到上游跑完才存在，所以运行期才解析（`app/work/handoff.py`）。
   声明的硬约束是**顺序保证**：上游必须是消费方的 DAG 祖先（H5，服务层校验）。
3. **回填只搬既有事实**：`drive_nodes.task_id` 由 `work_sessions.task_id`
   经 `work_session_id` 回填 —— 这是库里**已经写着**的事实（谁产出的），
   不是猜测。没有会话关联的文档保持 NULL。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "43a70cd19cc7"
down_revision: str | Sequence[str] | None = "c3e5a7b9d124"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ---- ① 产出归属（H2）：哪一列、可空、有索引（按 Task 反查产物是常用查询）----
    op.add_column("drive_nodes", sa.Column("task_id", sa.Integer(), nullable=True))
    op.create_index("ix_drive_nodes_task_id", "drive_nodes", ["task_id"])
    with op.batch_alter_table("drive_nodes") as batch:
        batch.create_foreign_key("fk_drive_nodes_task_id", "tasks", ["task_id"], ["id"])

    # ---- ② 预期交付物类型（H4）：JSON 列表，默认空 ----
    op.add_column(
        "tasks",
        sa.Column("produces_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )

    # ---- ③ 输入声明（H4/H5）：声明指向**上游 Task**，不指向 artifact ----
    op.create_table(
        "task_inputs",
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("source_task_id", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["source_task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "source_task_id", name="uq_task_inputs_pair"),
    )
    op.create_index("ix_task_inputs_task_id", "task_inputs", ["task_id"])
    op.create_index("ix_task_inputs_source", "task_inputs", ["source_task_id", "task_id"])

    # ---- ④ 使用事实（H3/G5）：artifact × task × session × 谁 ----
    op.create_table(
        "artifact_links",
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("work_session_id", sa.Integer(), nullable=True),
        sa.Column("actor_employee_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["actor_employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["artifact_id"], ["drive_nodes.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"]),
        sa.ForeignKeyConstraint(["work_session_id"], ["work_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_id", "task_id", "role", name="uq_artifact_links_artifact_task_role"
        ),
    )
    op.create_index("ix_artifact_links_artifact_id", "artifact_links", ["artifact_id"])
    op.create_index("ix_artifact_links_task_id", "artifact_links", ["task_id"])
    op.create_index("ix_artifact_links_artifact", "artifact_links", ["artifact_id", "role"])
    op.create_index("ix_artifact_links_task", "artifact_links", ["task_id", "role"])

    # ---- ⑤ 回填产出归属：库里已经写着的事实（work_sessions.task_id），不是猜测 ----
    op.execute(
        """
        UPDATE drive_nodes
           SET task_id = (
               SELECT work_sessions.task_id
                 FROM work_sessions
                WHERE work_sessions.id = drive_nodes.work_session_id
           )
         WHERE drive_nodes.work_session_id IS NOT NULL
           AND drive_nodes.task_id IS NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_artifact_links_task", table_name="artifact_links")
    op.drop_index("ix_artifact_links_artifact", table_name="artifact_links")
    op.drop_index("ix_artifact_links_task_id", table_name="artifact_links")
    op.drop_index("ix_artifact_links_artifact_id", table_name="artifact_links")
    op.drop_table("artifact_links")

    op.drop_index("ix_task_inputs_source", table_name="task_inputs")
    op.drop_index("ix_task_inputs_task_id", table_name="task_inputs")
    op.drop_table("task_inputs")

    op.drop_column("tasks", "produces_json")

    with op.batch_alter_table("drive_nodes") as batch:
        batch.drop_constraint("fk_drive_nodes_task_id", type_="foreignkey")
    op.drop_index("ix_drive_nodes_task_id", table_name="drive_nodes")
    op.drop_column("drive_nodes", "task_id")

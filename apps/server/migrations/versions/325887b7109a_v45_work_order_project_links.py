"""v45 work order project links (M2.9)

Revision ID: 325887b7109a
Revises: b3aee926425c
Create Date: 2026-09-12 23:58:00.000000

M2.9（docs/m2-implementation-plan.md §12 / 设计 §11.2，W23 / WO1–WO6）
—— **WorkOrder → Project 绑定边**。

连接商业需求与执行载体，**不把 WorkOrder 变成执行图**（W23）：

| 变更 | 内容 |
| --- | --- |
| `work_order_project_links`（新表）| 绑定边：`routed`（系统投递）/ `bound`（管理绑定）/ `declined`（管理拒绝）|
| `ix_work_orders_project_id` | 按项目反查订单（读面便宜）|

三条设计裁决写在迁移里：

1. **状态机不动**（J5）：WorkOrder 的 12 个状态一个不加、一个不减、不加必经步骤；
   桥只加一条**边**。`contracts.WORK_ORDER_STATES_FROZEN` 把这件事钉成可测的契约。
2. **指针 + 历史分开**：`work_orders.project_id` 是**当前绑定的指针**（读起来便宜），
   `work_order_project_links` 是**决定的历史**（J1 要"绑定或显式拒绝两条路径都有记录"）。
   两者由同一个函数在同一事务里写 —— 同一条事实不留两个写入者。
3. **回填只搬既有事实**：`work_orders.project_id` 本来就是一个自由字段；
   凡它**指向一个真实存在、且属于承接公司的 Project** 的订单，回填一条 `bound` 行
   （actor 为空、reason 标明来自回填）。指向不存在/别家项目的值**原样留着、不猜**
   —— 那正是本阶段要消灭的"无校验自由字段"，历史不能被美化成"它一直是合法的"。

另外刻意**不加** `work_orders.project_id` 的外键约束：列里可能已经存在历史脏值，
加 FK 会让迁移在真实库上失败或逼迫我们删数据。引用完整性由**桥的校验**保证
（新写入一律受校验，见 `app/work/work_order_bridge.py`）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "325887b7109a"
down_revision: str | Sequence[str] | None = "b3aee926425c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "work_order_project_links",
        sa.Column("work_order_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("actor_employee_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["actor_employee_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_work_order_project_links_work_order_id", "work_order_project_links", ["work_order_id"]
    )
    op.create_index("ix_work_order_project_links_order", "work_order_project_links", ["work_order_id", "id"])
    op.create_index("ix_work_order_project_links_project", "work_order_project_links", ["project_id", "id"])
    op.create_index("ix_work_orders_project_id", "work_orders", ["project_id"])

    # ---- 回填：只搬"指针真的指向本公司项目"这一个既有事实 ----
    # 承接公司口径 = `assignee_actor_kind='company'` 时的 `assignee_actor_ref`。
    op.execute(
        """
        INSERT INTO work_order_project_links
            (work_order_id, action, project_id, actor_employee_id, reason, metadata_json,
             created_at, updated_at)
        SELECT wo.id, 'bound', wo.project_id, NULL, 'backfill:v45',
               '{"source": "migration:v45", "note": "project_id was already set"}',
               wo.updated_at, wo.updated_at
          FROM work_orders AS wo
          JOIN projects AS p ON p.id = wo.project_id
         WHERE wo.project_id IS NOT NULL
           AND wo.assignee_actor_kind = 'company'
           AND p.company_id = wo.assignee_actor_ref
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_work_orders_project_id", table_name="work_orders")
    op.drop_index("ix_work_order_project_links_project", table_name="work_order_project_links")
    op.drop_index("ix_work_order_project_links_order", table_name="work_order_project_links")
    op.drop_index(
        "ix_work_order_project_links_work_order_id", table_name="work_order_project_links"
    )
    op.drop_table("work_order_project_links")

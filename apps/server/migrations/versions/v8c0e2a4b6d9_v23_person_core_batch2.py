"""v23: PersonCore R1.2 批次 2 —— knowledge_items 属主口径 person 化

Revision ID: v8c0e2a4b6d9
Revises: u7b9d1f3a5c8e
Create Date: 2026-09-10

依据 docs/person-core-migration.md §3 D4 三步法的第一步（加列回填）：
- knowledge_items 加 `owner_person_id` nullable 整型列（不加 FK，D3 纪律）。
  命名与既有 owner_employee_id 镜像对称（批次 1 的 employee_id→person_id 惯例
  在 owner_ 前缀列上的对应形式）；
- 内联回填：owner_employee_id → employees.person_id；owner_employee_id 为 NULL 的行
  （department/company scope 条目没有个人属主）跳过，owner_person_id 保持 NULL；
- 普通索引 ix_knowledge_items_owner_person_id（无既有唯一约束需要镜像）；
- scope / department_id 语义不动：公司/部门分层是公司上下文，不随人称切换。

兼容期 owner_employee_id 保留不删（deprecated 镜像）；API schema 不变。

Verify: up/down/up + alembic check；回填后 owner_employee_id 非空的行
owner_person_id 非空率 100%。
"""

import sqlalchemy as sa
from alembic import op

revision = "v8c0e2a4b6d9"
down_revision = "u7b9d1f3a5c8e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_items", sa.Column("owner_person_id", sa.Integer(), nullable=True))
    op.get_bind().execute(
        sa.text(
            "UPDATE knowledge_items SET owner_person_id = ("
            "SELECT person_id FROM employees e WHERE e.id = knowledge_items.owner_employee_id"
            ") WHERE owner_person_id IS NULL AND owner_employee_id IS NOT NULL"
        )
    )
    op.create_index(
        op.f("ix_knowledge_items_owner_person_id"), "knowledge_items", ["owner_person_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_knowledge_items_owner_person_id"), table_name="knowledge_items")
    op.drop_column("knowledge_items", "owner_person_id")

"""v22: PersonCore R1.1 批次 1 —— 人格与学习域 7 表挂 person_id

Revision ID: u7b9d1f3a5c8e
Revises: t6a8c0e2f4b6
Create Date: 2026-09-10

依据 docs/person-core-migration.md §3 D4 三步法（加列回填 → 双写 → 切读）的第一步：
employee_brains / memory_entries / skills / learning_records / learning_sessions /
skill_usages / learning_priorities 各加 `person_id` nullable 整型列（不加 FK，D3 纪律），
按 employees 连接内联回填，再补索引。

索引口径（服务层完整性 + 部分唯一索引惯例）：
- 五张多行表（memory_entries/skills/learning_records/skill_usages/learning_sessions）
  只加普通索引 ix_<table>_person_id；
- employee_brains 既有 employee_id 唯一（一人一脑）⇒ person 侧镜像为部分唯一索引
  uq_employee_brains_person（WHERE person_id IS NOT NULL）；
- learning_priorities 既有 uq(employee_id, topic) ⇒ person 侧镜像为部分唯一索引
  uq_learning_priority_person（WHERE person_id IS NOT NULL）。既有约束一律不动。

兼容期 employee_id 列保留不删（deprecated 镜像）；learning_sessions.company_id 是
公司上下文快照，与人称切换无关，不动。

Verify: up/down/up + alembic check；回填后 7 表 person_id 非空率 100%。
"""

import sqlalchemy as sa
from alembic import op

revision = "u7b9d1f3a5c8e"
down_revision = "t6a8c0e2f4b6"
branch_labels = None
depends_on = None

# (表, 普通索引名)；employee_brains / learning_priorities 走部分唯一索引，不在此列
_PLAIN_INDEXED_TABLES = (
    "memory_entries",
    "skills",
    "learning_records",
    "learning_sessions",
    "skill_usages",
)

_BACKFILL_SQL = (
    "UPDATE {table} SET person_id = ("
    "SELECT person_id FROM employees e WHERE e.id = {table}.employee_id"
    ") WHERE person_id IS NULL"
)


def upgrade() -> None:
    for table in (*_PLAIN_INDEXED_TABLES, "employee_brains", "learning_priorities"):
        op.add_column(table, sa.Column("person_id", sa.Integer(), nullable=True))
        op.get_bind().execute(sa.text(_BACKFILL_SQL.format(table=table)))

    for table in _PLAIN_INDEXED_TABLES:
        op.create_index(op.f(f"ix_{table}_person_id"), table, ["person_id"])
    op.create_index(
        "uq_employee_brains_person",
        "employee_brains",
        ["person_id"],
        unique=True,
        sqlite_where=sa.text("person_id IS NOT NULL"),
        postgresql_where=sa.text("person_id IS NOT NULL"),
    )
    op.create_index(
        "uq_learning_priority_person",
        "learning_priorities",
        ["person_id", "topic"],
        unique=True,
        sqlite_where=sa.text("person_id IS NOT NULL"),
        postgresql_where=sa.text("person_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_learning_priority_person", table_name="learning_priorities")
    op.drop_index("uq_employee_brains_person", table_name="employee_brains")
    for table in reversed(_PLAIN_INDEXED_TABLES):
        op.drop_index(op.f(f"ix_{table}_person_id"), table_name=table)
    for table in ("learning_priorities", "employee_brains", *reversed(_PLAIN_INDEXED_TABLES)):
        op.drop_column(table, "person_id")

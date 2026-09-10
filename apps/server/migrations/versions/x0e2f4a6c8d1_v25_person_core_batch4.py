"""v25: PersonCore R1.4 批次 4 —— 资源与署名域挂 person 镜像列

Revision ID: x0e2f4a6c8d1
Revises: w9d1f3b5c7e0
Create Date: 2026-09-10

依据 docs/person-core-migration.md §3 D4 三步法的第一步（加列回填）与 §2.2 裁定：
- runtime_instances.person_id（employee_id unique ⇒ 一人一实例在 person 口径上的
  部分唯一索引 uq_runtime_instances_person，照批次 1 employee_brains 的做法）；
- providers.owner_person_id / model_bindings.person_id / drive_nodes.owner_person_id /
  drive_revisions.author_person_id / artifacts.author_person_id /
  messages.sender_person_id + recipient_person_id /
  document_artifacts.author_person_id / review_meetings.presenter_person_id /
  events.actor_person_id：普通索引 ix_*，nullable、不加 FK（D3）；
- 回填：镜像列 ← 源列经 employees.person_id 解析；源列可空的行（NULL）跳过；
- **不动**：drive_collaborators.employee_id（协作授权，成员语义）、
  project_phases.owner_employee_id（工作分派，成员语义）、resource_assets、
  employments、tasks.assignee_id 等 —— 见方案 §2.2 下半区裁定。

Verify: up/down/up + alembic check；源列非空的行镜像列 100% 回填。
"""

import sqlalchemy as sa
from alembic import op

revision = "x0e2f4a6c8d1"
down_revision = "w9d1f3b5c7e0"
branch_labels = None
depends_on = None

# (表, 源列, 镜像列) —— 镜像命名惯例：<前缀>_employee_id ↔ <前缀>_person_id
_MIRRORED_COLUMNS = (
    ("providers", "owner_employee_id", "owner_person_id"),
    ("model_bindings", "employee_id", "person_id"),
    ("drive_nodes", "owner_employee_id", "owner_person_id"),
    ("drive_revisions", "author_employee_id", "author_person_id"),
    ("artifacts", "author_id", "author_person_id"),
    ("messages", "sender_id", "sender_person_id"),
    ("messages", "recipient_id", "recipient_person_id"),
    ("document_artifacts", "author_employee_id", "author_person_id"),
    ("review_meetings", "presenter_employee_id", "presenter_person_id"),
    ("events", "actor_employee_id", "actor_person_id"),
)

_BACKFILL_SQL = (
    "UPDATE {table} SET {mirror} = ("
    "SELECT person_id FROM employees e WHERE e.id = {table}.{source}"
    ") WHERE {mirror} IS NULL AND {source} IS NOT NULL"
)


def upgrade() -> None:
    op.add_column("runtime_instances", sa.Column("person_id", sa.Integer(), nullable=True))
    op.get_bind().execute(
        sa.text(_BACKFILL_SQL.format(
            table="runtime_instances", source="employee_id", mirror="person_id"
        ))
    )
    for table, source, mirror in _MIRRORED_COLUMNS:
        op.add_column(table, sa.Column(mirror, sa.Integer(), nullable=True))
        op.get_bind().execute(
            sa.text(_BACKFILL_SQL.format(table=table, source=source, mirror=mirror))
        )
        op.create_index(op.f(f"ix_{table}_{mirror}"), table, [mirror])
    op.create_index(
        "uq_runtime_instances_person",
        "runtime_instances",
        ["person_id"],
        unique=True,
        sqlite_where=sa.text("person_id IS NOT NULL"),
        postgresql_where=sa.text("person_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_runtime_instances_person", table_name="runtime_instances")
    for table, _source, mirror in reversed(_MIRRORED_COLUMNS):
        op.drop_index(op.f(f"ix_{table}_{mirror}"), table_name=table)
        op.drop_column(table, mirror)
    op.drop_column("runtime_instances", "person_id")

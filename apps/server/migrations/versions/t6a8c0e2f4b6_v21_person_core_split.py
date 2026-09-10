"""v21: PersonCore 拆分 R1.0 —— persons 表 + employees.person_id 兼容层

Revision ID: t6a8c0e2f4b6
Revises: s5a7c9e1f3b5
Create Date: 2026-09-10

依据 docs/person-core-migration.md §3 D1/D2/D3：
- persons 是「人」的聚合根（身份 + 命名），保持最小列集，不放 JSON 万能字段（D4.1）；
- 每个现存 employee 在迁移内联回填一个对应 person（slug/name/avatar/username 照抄，
  时间戳原样保留），再按 slug 回连 employees.person_id —— slug 双表全局唯一，
  是兼容期唯一无歧义的连接键；不靠应用层补数（D2）；
- 此后 persons.slug 是唯一权威，employees.slug 保留为镜像列（读口径切换前不动）；
- SQLite 纪律（D3）：person_id 裸 nullable、不加 FK（项目不开 PRAGMA foreign_keys，
  匿名 FK 重建会炸）；1-1 完整性靠部分唯一索引 uq_employees_person_id
  （WHERE person_id IS NOT NULL），同一索引必须同步写在模型定义里，否则
  alembic check 漂移守卫会红。

Verify: up/down/up + alembic check；回填后 employees.person_id 非空率 100%、
persons 行数 = employees 行数。
"""

import sqlalchemy as sa
from alembic import op

revision = "t6a8c0e2f4b6"
down_revision = "s5a7c9e1f3b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persons",
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("avatar", sa.String(length=500), nullable=True),
        sa.Column("username", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.add_column("employees", sa.Column("person_id", sa.Integer(), nullable=True))
    op.create_index(
        "uq_employees_person_id",
        "employees",
        ["person_id"],
        unique=True,
        sqlite_where=sa.text("person_id IS NOT NULL"),
        postgresql_where=sa.text("person_id IS NOT NULL"),
    )

    # 内联回填（D2）：迁移只跑一次，但 SQL 写成干净的「判空才动」形式。
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT slug, name, avatar, username, created_at, updated_at"
            " FROM employees WHERE person_id IS NULL ORDER BY id"
        )
    ).fetchall()
    for slug, name, avatar, username, created_at, updated_at in rows:
        conn.execute(
            sa.text(
                "INSERT INTO persons (slug, name, avatar, username, created_at, updated_at)"
                " VALUES (:slug, :name, :avatar, :username, :created_at, :updated_at)"
            ),
            {
                "slug": slug,
                "name": name,
                "avatar": avatar,
                "username": username,
                "created_at": created_at,
                "updated_at": updated_at,
            },
        )
    conn.execute(
        sa.text(
            "UPDATE employees SET person_id = ("
            "SELECT id FROM persons WHERE persons.slug = employees.slug"
            ") WHERE person_id IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_index("uq_employees_person_id", table_name="employees")
    op.drop_column("employees", "person_id")
    op.drop_table("persons")

"""v41 position authority grants & role resources (M2.2)

Revision ID: b2d4f6a8c013
Revises: a1c2e3f40517
Create Date: 2026-09-11 22:10:00.000000

M2.2（docs/m2-implementation-plan.md §5）—— **Authority Projection** 与
**Role Resource Index** 的落地点。

用户拍板（方案 A）：新增 `position_authority_grants` 薄表；
`position_definition_packages` **保持**纯 Resource Provisioning / Entitlement 语义，
不承载管理授权。

**纯 additive**（不改动任何既有列、不加 FK 到既有表）：

| 对象 | 语义 |
| --- | --- |
| `position_authority_grants` | 职位**管理授权**（硬边界）；append-only + 时间窗；default-deny |
| `position_definition_resources` | Role Resource Index（**指针**，内容仍在 knowledge_items / drive_nodes） |
| `position_definitions.advisory_scope` | 该职位**通常**做什么工作（advisory，非工作边界） |

三条设计裁决写在迁移里，避免以后有人"顺手"改掉：

1. `scope_ref` 用 **0 哨兵**而不是 NULL —— SQLite 唯一索引把 NULL 视为互不相等，
   用 NULL 会让 `uq_position_authority_active`（同一职位同一授权至多一条生效行）
   静默失效；
2. `uq_position_authority_active` 是部分唯一索引（`WHERE effective_to IS NULL`），
   它必须同时写进模型，否则 `alembic check` 会把它当成库里多出来的东西要求删除；
3. `server_default` 一律用 `sa.text(...)` 而不是普通字符串：普通字符串会被
   SQLAlchemy 再包一层引号，SQLite 里就变成 `DEFAULT '''company'''` ——
   存进去的是 `'company'`（带引号），读回来 JSON 解析直接炸。模型侧写的是
   `text("'company'")`，迁移侧必须渲染出**同一个** SQL 字面量。
4. **不做数据回填**：default-deny 是设计本身。历史任职不会"因为以前存在过"
   而获得授权 —— 授权必须由公司显式声明（种子见 `app/work/authority_seed.py`）。
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2d4f6a8c013"
down_revision = "a1c2e3f40517"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "position_definitions",
        sa.Column("advisory_scope", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )

    op.create_table(
        "position_authority_grants",
        sa.Column("position_definition_id", sa.Integer(), nullable=False),
        sa.Column("authority_kind", sa.String(length=40), nullable=False),
        sa.Column(
            "scope_kind",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'company'"),
        ),
        sa.Column("scope_ref", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_amount", sa.Integer(), nullable=True),
        sa.Column("effective_from", sa.DateTime(), nullable=False),
        sa.Column("effective_to", sa.DateTime(), nullable=True),
        sa.Column("supersedes_grant_id", sa.Integer(), nullable=True),
        sa.Column("granted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False, server_default=sa.text("''")),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["position_definition_id"], ["position_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_position_authority_grants_position_definition_id",
        "position_authority_grants",
        ["position_definition_id"],
    )
    op.create_index(
        "ix_position_authority_grants_authority_kind",
        "position_authority_grants",
        ["authority_kind"],
    )
    op.create_index(
        "ix_position_authority_grants_effective_to",
        "position_authority_grants",
        ["effective_to"],
    )
    op.create_index(
        "uq_position_authority_active",
        "position_authority_grants",
        ["position_definition_id", "authority_kind", "scope_kind", "scope_ref"],
        unique=True,
        sqlite_where=sa.text("effective_to IS NULL"),
        postgresql_where=sa.text("effective_to IS NULL"),
    )

    op.create_table(
        "position_definition_resources",
        sa.Column("position_definition_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("ref", sa.String(length=300), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["position_definition_id"], ["position_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "position_definition_id", "kind", "ref", name="uq_position_resource_ref"
        ),
    )
    op.create_index(
        "ix_position_definition_resources_position_definition_id",
        "position_definition_resources",
        ["position_definition_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_position_definition_resources_position_definition_id",
        table_name="position_definition_resources",
    )
    op.drop_table("position_definition_resources")

    op.drop_index("uq_position_authority_active", table_name="position_authority_grants")
    op.drop_index(
        "ix_position_authority_grants_effective_to", table_name="position_authority_grants"
    )
    op.drop_index(
        "ix_position_authority_grants_authority_kind", table_name="position_authority_grants"
    )
    op.drop_index(
        "ix_position_authority_grants_position_definition_id",
        table_name="position_authority_grants",
    )
    op.drop_table("position_authority_grants")

    op.drop_column("position_definitions", "advisory_scope")

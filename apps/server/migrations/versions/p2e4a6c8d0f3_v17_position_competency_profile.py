"""v17.position_competency_profile —— 岗位能力画像（P7）

add table `position_profile_versions`（版本化：draft/active/retired，部分唯一索引保证
单 ACTIVE）；升级 `position_competency_requirements`（requirement_type / target_score /
minimum_confidence / critical / priority / notes / position_profile_version_id +
按 (version, competency) 的唯一索引）；`position_definitions` 加 `assessment_profile_id`
（绑定“这个岗位通常如何考核”；能力结果仍只能来自 Evidence→Assessment）。

兼容性（不破坏 P6）：
- 旧 `uq_position_competency_requirement (position_definition_id, competency_definition_id)`
  保留（新写入 position_definition_id 置 NULL，SQLite 的 NULL 唯一是分离的 ⇒
  多版本可各自持有同一 competency；真正唯一由新唯一索引按版本+能力保证）—— 避免
  SQLite 无法 ALTER 加/删约束而被迫整表重建。
- `position_competency_requirements` 存量行若无版本归属，仍可读（旧语义），
  但不参与新画像（P7 服务只读版本行）。

Verify: upgrade head → downgrade -1 → upgrade head；alembic check 无漂移；
历史 PositionDefinition（无画像）不受影响（Not Configured 而非猜测）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "p2e4a6c8d0f3"
down_revision: str | Sequence[str] | None = "o1f2e3d4c5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "position_profile_versions",
        sa.Column(
            "position_definition_id",
            sa.Integer(),
            sa.ForeignKey("position_definitions.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("effective_from", sa.DateTime(), nullable=True),
        sa.Column("effective_to", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("published_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("published_note", sa.String(length=500), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "position_definition_id", "version", name="uq_position_profile_version"
        ),
    )
    op.create_index(
        op.f("ix_position_profile_versions_position_definition_id"),
        "position_profile_versions",
        ["position_definition_id"],
    )
    # 单 ACTIVE：部分唯一索引（只约束 status='active'）
    op.create_index(
        "uq_position_profile_active",
        "position_profile_versions",
        ["position_definition_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    # 不加 FK（SQLite 无 ALTER CONSTRAINT 支持；完整性由服务层 + 唯一索引保证）
    op.add_column(
        "position_competency_requirements",
        sa.Column("position_profile_version_id", sa.Integer(), nullable=True),
    )
    # 旧版本模型变化需要 batch（SQLite 复制重建）：`required` bool 退役（由
    # requirement_type 取代）、position_definition_id 改为可空（新写入不填，NULL
    # 让旧 def 级唯一约束不再阻塞多版本持有同一 competency）。表实际为空，重建安全。
    with op.batch_alter_table("position_competency_requirements") as batch_op:
        batch_op.drop_column("required")
        batch_op.alter_column(
            "position_definition_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
    op.add_column(
        "position_competency_requirements",
        sa.Column(
            "requirement_type",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'required'"),
        ),
    )
    op.add_column(
        "position_competency_requirements",
        sa.Column("target_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "position_competency_requirements",
        sa.Column("minimum_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "position_competency_requirements",
        sa.Column("critical", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "position_competency_requirements",
        sa.Column("priority", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "position_competency_requirements",
        sa.Column("notes", sa.String(length=500), nullable=False, server_default=sa.text("''")),
    )
    op.create_index(
        op.f("ix_position_competency_requirements_position_profile_version_id"),
        "position_competency_requirements",
        ["position_profile_version_id"],
    )
    # 版本内 competency 唯一（新语义；旧 def 级唯一保留 + NULL 分离兼容）
    op.create_index(
        "uq_position_profile_requirement",
        "position_competency_requirements",
        ["position_profile_version_id", "competency_definition_id"],
        unique=True,
    )
    # 不加 FK（SQLite 无 ALTER CONSTRAINT；由服务层校验）
    op.add_column(
        "position_definitions",
        sa.Column("assessment_profile_id", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("position_definitions", "assessment_profile_id")
    op.drop_index("uq_position_profile_requirement", table_name="position_competency_requirements")
    op.drop_index(
        op.f("ix_position_competency_requirements_position_profile_version_id"),
        table_name="position_competency_requirements",
    )
    op.drop_column("position_competency_requirements", "notes")
    op.drop_column("position_competency_requirements", "priority")
    op.drop_column("position_competency_requirements", "critical")
    op.drop_column("position_competency_requirements", "minimum_confidence")
    op.drop_column("position_competency_requirements", "target_score")
    op.drop_column("position_competency_requirements", "requirement_type")
    # 还原 v16 语义（升级里 batch 撤掉的）：`required` 列回来 + position_definition_id 恢复 NOT NULL
    with op.batch_alter_table("position_competency_requirements") as batch_op:
        batch_op.add_column(
            sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.text("1"))
        )
        batch_op.alter_column(
            "position_definition_id",
            existing_type=sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        )
    op.drop_column("position_competency_requirements", "position_profile_version_id")
    op.drop_index("uq_position_profile_active", table_name="position_profile_versions")
    op.drop_index(
        op.f("ix_position_profile_versions_position_definition_id"),
        table_name="position_profile_versions",
    )
    op.drop_table("position_profile_versions")

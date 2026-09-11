"""v33: M1.2 奖励发放 —— reward_grants

Revision ID: 691816bccb53
Revises: 8f1abef8410f
Create Date: 2026-09-11

依据 docs/m1-economy-design.md §14/§15/§16 与 plan §4/M1.2。**只建表**（不改既有表；
账本底座 v32 由 M1.1 提供，本迁移只加"奖励的资格/发放记录"）。

- `uq_reward_grant_identity(reward_type, actor_kind, actor_ref, reference_key)`：
  重复领取由唯一约束收敛（E10）—— 服务层命中即返回既有 grant，绝不二次 mint；
- `reference_key`：同一 actor 在同一奖励类型下的"这一次"（`daily:2026-09-11` /
  `achievement:first_employee` / `recovery:2026-09-11` / `tutorial:<id>`）；
- `amount` + `policy_version`：发放时的政策快照（日后调政策不改历史）；
- `ledger_transaction_id`：指向真正把钱发出去的那笔交易（E16 可追溯）；
- 系统账户（ISSUANCE/TREASURY/BURN）**不建种子行**：`MonetaryAuthority.ensure_system_accounts()`
  在首次发行时幂等创建（M1.1 已实现并有测试）。

Verify: up/down/up + `alembic check`。
"""

import sqlalchemy as sa
from alembic import op

revision = "691816bccb53"
down_revision = "8f1abef8410f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reward_grants",
        sa.Column("reward_type", sa.String(length=32), nullable=False),
        sa.Column("actor_kind", sa.String(length=20), nullable=False),
        sa.Column("actor_ref", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("reference_key", sa.String(length=120), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("ledger_transaction_id", sa.Integer(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["ledger_transaction_id"], ["ledger_transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "reward_type",
            "actor_kind",
            "actor_ref",
            "reference_key",
            name="uq_reward_grant_identity",
        ),
    )
    op.create_index(
        "ix_reward_grants_actor", "reward_grants", ["actor_kind", "actor_ref", "reward_type"]
    )
    op.create_index("ix_reward_grants_company", "reward_grants", ["company_id", "id"])
    op.create_index("ix_reward_grants_reward_type", "reward_grants", ["reward_type"])
    op.create_index("ix_reward_grants_status", "reward_grants", ["status"])


def downgrade() -> None:
    op.drop_index("ix_reward_grants_status", table_name="reward_grants")
    op.drop_index("ix_reward_grants_reward_type", table_name="reward_grants")
    op.drop_index("ix_reward_grants_company", table_name="reward_grants")
    op.drop_index("ix_reward_grants_actor", table_name="reward_grants")
    op.drop_table("reward_grants")

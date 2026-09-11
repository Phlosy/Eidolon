"""v32: M1.1 账本底座 —— ledger_accounts / ledger_transactions / ledger_entries / wallet_projection

Revision ID: 8f1abef8410f
Revises: d6e8f0a2b4c7
Create Date: 2026-09-11

依据 docs/m1-economy-design.md §10–§12b 与 plan §4/M1.1a。**只建表，不改任何既有表**
（M1.1 不触碰 T2：Person / Character / Market / Fit / Recruitment / Knowledge 一行不改）。

- `ledger_accounts`：账户 = 主体 × 货币 × kind × subject_ref。
  唯一约束 `uq_ledger_account_identity(actor_kind, actor_ref, currency, kind, subject_ref)`：
  系统账户（`kind ∈ issuance/treasury/burn`）靠它保证 bootstrap 幂等；
  Escrow 账户用 `subject_ref = escrow id` 区分，**不设全局 SYSTEM_ESCROW 池**。
  `normal_side` 由 kind 派生落库（E26 的唯一解释入口）。
- `ledger_transactions`：账务信封。部分唯一索引
  `uq_ledger_transaction_idempotency(idempotency_key) WHERE idempotency_key IS NOT NULL`
  —— 重复提交同 key 由约束收敛（E10/E12 同族，绝不二次过账）。
- `ledger_entries`：借贷腿，**append-only**（刻意没有 `updated_at`；E17/E31）。
  金额 > 0 由服务层校验（方向只能由 direction 表达）。
- `wallet_projection`：可重建物化投影（`account_id` 为主键），承载快速余额查询与 CAS 并发控制；
  清空后必须能由账本三表完整重算（E29）。

Verify: up/down/up 实测 + `alembic check` 无漂移。
"""

import sqlalchemy as sa
from alembic import op

revision = "8f1abef8410f"
down_revision = "d6e8f0a2b4c7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ledger_accounts",
        sa.Column("actor_kind", sa.String(length=20), nullable=False),
        sa.Column("actor_ref", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("subject_ref", sa.Integer(), nullable=False),
        sa.Column("normal_side", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("frozen_reason", sa.String(length=200), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "actor_kind",
            "actor_ref",
            "currency",
            "kind",
            "subject_ref",
            name="uq_ledger_account_identity",
        ),
    )
    op.create_index(
        "ix_ledger_accounts_actor", "ledger_accounts", ["actor_kind", "actor_ref"]
    )
    op.create_index("ix_ledger_accounts_status", "ledger_accounts", ["status"])

    op.create_table(
        "ledger_transactions",
        sa.Column("transaction_type", sa.String(length=24), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=True),
        sa.Column("reference_type", sa.String(length=40), nullable=False),
        sa.Column("reference_id", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("initiated_by_kind", sa.String(length=20), nullable=True),
        sa.Column("initiated_by_ref", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("posted_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ledger_transactions_reference",
        "ledger_transactions",
        ["reference_type", "reference_id"],
    )
    op.create_index("ix_ledger_transactions_status", "ledger_transactions", ["status"])
    op.create_index(
        "ix_ledger_transactions_transaction_type", "ledger_transactions", ["transaction_type"]
    )
    op.create_index(
        "uq_ledger_transaction_idempotency",
        "ledger_transactions",
        ["idempotency_key"],
        unique=True,
        sqlite_where=sa.text("idempotency_key IS NOT NULL"),
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("transaction_id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["ledger_accounts.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["ledger_transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_entries_account", "ledger_entries", ["account_id", "id"])
    op.create_index("ix_ledger_entries_transaction_id", "ledger_entries", ["transaction_id"])

    op.create_table(
        "wallet_projection",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("posted_balance", sa.Integer(), nullable=False),
        sa.Column("available_balance", sa.Integer(), nullable=False),
        sa.Column("reserved_balance", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_entry_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["ledger_accounts.id"]),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_index(
        "ix_wallet_projection_available_balance", "wallet_projection", ["available_balance"]
    )


def downgrade() -> None:
    op.drop_index("ix_wallet_projection_available_balance", table_name="wallet_projection")
    op.drop_table("wallet_projection")
    op.drop_index("ix_ledger_entries_transaction_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_account", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("uq_ledger_transaction_idempotency", table_name="ledger_transactions")
    op.drop_index("ix_ledger_transactions_transaction_type", table_name="ledger_transactions")
    op.drop_index("ix_ledger_transactions_status", table_name="ledger_transactions")
    op.drop_index("ix_ledger_transactions_reference", table_name="ledger_transactions")
    op.drop_table("ledger_transactions")
    op.drop_index("ix_ledger_accounts_status", table_name="ledger_accounts")
    op.drop_index("ix_ledger_accounts_actor", table_name="ledger_accounts")
    op.drop_table("ledger_accounts")

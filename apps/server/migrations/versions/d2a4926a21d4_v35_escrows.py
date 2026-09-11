"""v35: M1.4 玩家工作市场 —— escrows

Revision ID: d2a4926a21d4
Revises: 328fbe9f3034
Create Date: 2026-09-11

依据 docs/m1-economy-design.md §19/§23/§24 与 plan §4/M1.4。**只建表**（不改既有表）。

- `escrows`：玩家之间的资金托管。`payer` 出资 → 条件满足 → `payee` 收款（release）
  或 `payer` 退回（refund）；**资金既不属于付款人也不属于收款人**（E7），Total Supply 不变；
- 每个 Escrow 一个独立托管账户：`escrow_account_id` 指向的账本行 `kind=escrow`、
  `subject_ref = escrow.id`（`ledger_accounts` 的唯一约束含 subject_ref，M1.1 已冻结）；
- `uq_escrow_work_order`：一个订单一个 Escrow（重复发布/重试由唯一约束收敛）；
- `status`：`UNFUNDED → FUNDED → RELEASED | REFUNDED | EXPIRED`（M1.0 冻结状态机）；
  **release 与 refund 的竞争由 CAS 裁定**（§33：只有一个能成功）；
- 三笔交易指针（funded/released/refunded）让每笔托管都能追溯到具体账本交易（E16）。

Verify: up/down/up + `alembic check`。
"""

import sqlalchemy as sa
from alembic import op

revision = "d2a4926a21d4"
down_revision = "328fbe9f3034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "escrows",
        sa.Column("work_order_id", sa.Integer(), nullable=True),
        sa.Column("payer_actor_kind", sa.String(length=20), nullable=False),
        sa.Column("payer_actor_ref", sa.Integer(), nullable=False),
        sa.Column("payee_actor_kind", sa.String(length=20), nullable=True),
        sa.Column("payee_actor_ref", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("escrow_account_id", sa.Integer(), nullable=True),
        sa.Column("funded_transaction_id", sa.Integer(), nullable=True),
        sa.Column("released_transaction_id", sa.Integer(), nullable=True),
        sa.Column("refunded_transaction_id", sa.Integer(), nullable=True),
        sa.Column("funded_at", sa.DateTime(), nullable=True),
        sa.Column("released_at", sa.DateTime(), nullable=True),
        sa.Column("refunded_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["escrow_account_id"], ["ledger_accounts.id"]),
        sa.ForeignKeyConstraint(["funded_transaction_id"], ["ledger_transactions.id"]),
        sa.ForeignKeyConstraint(["refunded_transaction_id"], ["ledger_transactions.id"]),
        sa.ForeignKeyConstraint(["released_transaction_id"], ["ledger_transactions.id"]),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_order_id", name="uq_escrow_work_order"),
    )
    op.create_index("ix_escrows_payer", "escrows", ["payer_actor_kind", "payer_actor_ref"])
    op.create_index("ix_escrows_status", "escrows", ["status"])
    op.create_index("ix_escrows_status_expires", "escrows", ["status", "expires_at"])


def downgrade() -> None:
    op.drop_index("ix_escrows_status_expires", table_name="escrows")
    op.drop_index("ix_escrows_status", table_name="escrows")
    op.drop_index("ix_escrows_payer", table_name="escrows")
    op.drop_table("escrows")

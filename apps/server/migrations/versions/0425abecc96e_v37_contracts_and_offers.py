"""v37: M1.6 合同核心 —— contracts / offers（+ escrows.contract_id）

Revision ID: 0425abecc96e
Revises: d9545a745166
Create Date: 2026-09-11

依据 docs/m1-economy-design.md §21/§22/§23/§24 与 plan §4/M1.6。

- `contracts`：通用商业合同（工作/人才/服务/采购/科研共用一个核心）。**对价
  `consideration_amount` 是一等列**（不塞 `terms_json`）；双方用 actor 列表达
  （issuer 必填、contractor 接受后回填）；`status` 走 M1.0 冻结状态机（§37）；
  `settlement_transaction_id` 指向终局交易（E16）；结算幂等靠
  `settlement_key = contract:<id>`（E12）；
- `offers`：出价/申请（人才出价、合同申请、报价共用）。**Offer 本身不产生资金流**，
  被接受后生成 Contract（`contract_id` 回填，§22）；锚点三选一（work_order / listing / 无）；
- `escrows.contract_id`：合同托管复用 M1.4 的托管表，**权威指针在 escrows 上**
  （部分唯一索引 `uq_escrow_contract` 保证一合同一托管）——刻意不在 contracts 上再放
  `escrow_id`，两个指针会漂移。SQLite 不支持 ALTER 加约束，因此走 **batch_alter_table**。

Verify: up/down/up + `alembic check`。
"""

import sqlalchemy as sa
from alembic import op

revision = "0425abecc96e"
down_revision = "d9545a745166"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contracts",
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("contract_type", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("terms_json", sa.JSON(), nullable=False),
        sa.Column("consideration_amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("issuer_actor_kind", sa.String(length=20), nullable=False),
        sa.Column("issuer_actor_ref", sa.Integer(), nullable=False),
        sa.Column("contractor_actor_kind", sa.String(length=20), nullable=True),
        sa.Column("contractor_actor_ref", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("reference_type", sa.String(length=40), nullable=False),
        sa.Column("reference_id", sa.String(length=64), nullable=False),
        sa.Column("effective_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(), nullable=True),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.Column("settlement_transaction_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["settlement_transaction_id"], ["ledger_transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(
        "ix_contracts_contractor",
        "contracts",
        ["contractor_actor_kind", "contractor_actor_ref"],
    )
    op.create_index(
        "ix_contracts_issuer", "contracts", ["issuer_actor_kind", "issuer_actor_ref"]
    )
    op.create_index("ix_contracts_status", "contracts", ["status"])
    op.create_index("ix_contracts_status_expires", "contracts", ["status", "expires_at"])

    op.create_table(
        "offers",
        sa.Column("contract_type", sa.String(length=20), nullable=False),
        sa.Column("work_order_id", sa.Integer(), nullable=True),
        sa.Column("listing_id", sa.Integer(), nullable=True),
        sa.Column("from_actor_kind", sa.String(length=20), nullable=False),
        sa.Column("from_actor_ref", sa.Integer(), nullable=False),
        sa.Column("to_actor_kind", sa.String(length=20), nullable=True),
        sa.Column("to_actor_ref", sa.Integer(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("terms_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("responded_at", sa.DateTime(), nullable=True),
        sa.Column("contract_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        sa.ForeignKeyConstraint(["work_order_id"], ["work_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_offers_work_order", "offers", ["work_order_id", "id"])
    op.create_index("ix_offers_listing", "offers", ["listing_id", "id"])
    op.create_index("ix_offers_from", "offers", ["from_actor_kind", "from_actor_ref"])
    op.create_index("ix_offers_status", "offers", ["status"])

    # 合同托管：SQLite 不支持 ALTER 加 FK ⇒ batch 重建表
    with op.batch_alter_table("escrows") as batch:
        batch.add_column(sa.Column("contract_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_escrows_contract_id", "contracts", ["contract_id"], ["id"]
        )
    op.create_index(
        "uq_escrow_contract",
        "escrows",
        ["contract_id"],
        unique=True,
        sqlite_where=sa.text("contract_id IS NOT NULL"),
        postgresql_where=sa.text("contract_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_escrow_contract", table_name="escrows")
    with op.batch_alter_table("escrows") as batch:
        batch.drop_constraint("fk_escrows_contract_id", type_="foreignkey")
        batch.drop_column("contract_id")
    op.drop_index("ix_offers_status", table_name="offers")
    op.drop_index("ix_offers_from", table_name="offers")
    op.drop_index("ix_offers_listing", table_name="offers")
    op.drop_index("ix_offers_work_order", table_name="offers")
    op.drop_table("offers")
    op.drop_index("ix_contracts_status_expires", table_name="contracts")
    op.drop_index("ix_contracts_status", table_name="contracts")
    op.drop_index("ix_contracts_issuer", table_name="contracts")
    op.drop_index("ix_contracts_contractor", table_name="contracts")
    op.drop_table("contracts")

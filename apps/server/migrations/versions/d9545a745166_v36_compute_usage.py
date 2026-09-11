"""v36: M1.5 公司经营经济 —— compute_usage + ledger_transactions.category

Revision ID: d9545a745166
Revises: d2a4926a21d4
Create Date: 2026-09-11

依据 docs/m1-economy-design.md §7/§25/§26 与 plan §4/M1.5。

- `compute_usage`：算力计量（**持续型 Sink 的事实来源**）—— 计量总是发生，扣款尽力而为
  （`status ∈ {paid, unpaid}`；`unpaid` 不是免费，而是未清成本，且**绝不产生负余额**，E24）；
  `idempotency_key` 唯一：同一会话/任务重放不会重复扣款（E12 同族）；
  保留 `provider_id/model/tokens/duration_seconds` 以便未来接真实 provider 成本映射（§26）；
- `ledger_transactions.category`：**业务类别一等列**（`EconomicCategory`）——
  报表/观测按类别聚合（避免另建同义表，plan §4/M1.5 的口径）。存量行为 NULL（未分类），
  不做数据回填：历史交易的类别信息本来就不存在，编造它才是真错误；
- 索引：`category`（报表过滤）、`(company_id, id)`、`status`。

Verify: up/down/up + `alembic check`。
"""

import sqlalchemy as sa
from alembic import op

revision = "d9545a745166"
down_revision = "d2a4926a21d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "compute_usage",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("employee_id", sa.Integer(), nullable=True),
        sa.Column("work_session_id", sa.Integer(), nullable=True),
        sa.Column("runtime_instance_id", sa.Integer(), nullable=True),
        sa.Column("provider_id", sa.Integer(), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("tokens", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("unpaid_reason", sa.String(length=60), nullable=False),
        sa.Column("treasury_transaction_id", sa.Integer(), nullable=True),
        sa.Column("burn_transaction_id", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["burn_transaction_id"], ["ledger_transactions.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["treasury_transaction_id"], ["ledger_transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_compute_usage_idempotency"),
    )
    op.create_index("ix_compute_usage_company", "compute_usage", ["company_id", "id"])
    op.create_index("ix_compute_usage_company_id", "compute_usage", ["company_id"])
    op.create_index("ix_compute_usage_status", "compute_usage", ["status"])

    op.add_column(
        "ledger_transactions", sa.Column("category", sa.String(length=32), nullable=True)
    )
    op.create_index(
        "ix_ledger_transactions_category", "ledger_transactions", ["category"]
    )


def downgrade() -> None:
    op.drop_index("ix_ledger_transactions_category", table_name="ledger_transactions")
    with op.batch_alter_table("ledger_transactions") as batch:
        batch.drop_column("category")
    op.drop_index("ix_compute_usage_status", table_name="compute_usage")
    op.drop_index("ix_compute_usage_company_id", table_name="compute_usage")
    op.drop_index("ix_compute_usage_company", table_name="compute_usage")
    op.drop_table("compute_usage")

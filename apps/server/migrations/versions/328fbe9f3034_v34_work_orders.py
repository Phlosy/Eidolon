"""v34: M1.3 官方工作市场 —— work_orders / work_order_submissions / evaluations

Revision ID: 328fbe9f3034
Revises: 691816bccb53
Create Date: 2026-09-11

依据 docs/m1-economy-design.md §17/§18/§20/§24 与 plan §4/M1.3。**只建表**（不改既有表）。

- `work_orders`：统一工作订单。金额/状态/双方/期限/资助模式都是**一等列**（设计 §17：
  核心领域字段不得塞进 JSON）；`settlement_transaction_id` 指向结算交易
  （结算幂等靠 `settlement_key` = ledger `idempotency_key` 的唯一约束，E12 —— 不另建
  settlements 表，与 plan §5 迁移路线一致）；
- `work_order_submissions`：每次提交一条（被拒后可重提，`attempt` 递增）；
- `evaluations`：验收记录（criteria/score/verdict/bonuses）—— **只判定，不改钱**（§20）。

索引：`(status, deadline_at)`（到期扫描）、`(kind, status)`（按类型找在招订单）、
`(assignee_actor_kind, assignee_actor_ref)`（"我承接的"）、`(order_id, id)`（提交/验收按序读）。

Verify: up/down/up + `alembic check`。
"""

import sqlalchemy as sa
from alembic import op

revision = "328fbe9f3034"
down_revision = "691816bccb53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_orders",
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("requirements_json", sa.JSON(), nullable=False),
        sa.Column("deliverables_json", sa.JSON(), nullable=False),
        sa.Column("reward_amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("issuer_actor_kind", sa.String(length=20), nullable=False),
        sa.Column("issuer_actor_ref", sa.Integer(), nullable=False),
        sa.Column("funding_mode", sa.String(length=20), nullable=False),
        sa.Column("evaluation_mode", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("deadline_at", sa.DateTime(), nullable=True),
        sa.Column("assignee_actor_kind", sa.String(length=20), nullable=True),
        sa.Column("assignee_actor_ref", sa.Integer(), nullable=True),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("settlement_transaction_id", sa.Integer(), nullable=True),
        sa.Column("settled_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["settlement_transaction_id"], ["ledger_transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(
        "ix_work_orders_assignee", "work_orders", ["assignee_actor_kind", "assignee_actor_ref"]
    )
    op.create_index("ix_work_orders_kind_status", "work_orders", ["kind", "status"])
    op.create_index("ix_work_orders_status", "work_orders", ["status"])
    op.create_index("ix_work_orders_status_deadline", "work_orders", ["status", "deadline_at"])

    op.create_table(
        "work_order_submissions",
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("deliverables_json", sa.JSON(), nullable=False),
        sa.Column("artifact_refs", sa.JSON(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["work_orders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_work_order_submissions_order", "work_order_submissions", ["order_id", "id"]
    )
    op.create_index(
        "ix_work_order_submissions_order_id", "work_order_submissions", ["order_id"]
    )

    op.create_table(
        "evaluations",
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(length=12), nullable=False),
        sa.Column("criteria_json", sa.JSON(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(length=12), nullable=False),
        sa.Column("bonuses_json", sa.JSON(), nullable=False),
        sa.Column("evaluated_by_actor_kind", sa.String(length=20), nullable=False),
        sa.Column("evaluated_by_actor_ref", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["work_orders.id"]),
        sa.ForeignKeyConstraint(["submission_id"], ["work_order_submissions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evaluations_order", "evaluations", ["order_id", "id"])
    op.create_index("ix_evaluations_order_id", "evaluations", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_evaluations_order_id", table_name="evaluations")
    op.drop_index("ix_evaluations_order", table_name="evaluations")
    op.drop_table("evaluations")
    op.drop_index("ix_work_order_submissions_order_id", table_name="work_order_submissions")
    op.drop_index("ix_work_order_submissions_order", table_name="work_order_submissions")
    op.drop_table("work_order_submissions")
    op.drop_index("ix_work_orders_status_deadline", table_name="work_orders")
    op.drop_index("ix_work_orders_status", table_name="work_orders")
    op.drop_index("ix_work_orders_kind_status", table_name="work_orders")
    op.drop_index("ix_work_orders_assignee", table_name="work_orders")
    op.drop_table("work_orders")

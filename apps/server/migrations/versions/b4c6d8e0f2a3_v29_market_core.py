"""v29: T2.3 市场核心 —— market_participants + market_listings

Revision ID: b4c6d8e0f2a3
Revises: a3b5c7d9e1f4
Create Date: 2026-09-10

依据 docs/t2-talent-market-design.md §7 D8 / plan §4.4。

- `market_participants`：市场参与者（player_company / npc_company / system_issuer）。
  NPC **不进 `companies`**（D8）；player_company 行带 `company_id` FK。
  部分唯一索引 `uq_market_participant_company(kind, company_id) WHERE company_id IS NOT NULL`
  —— 幂等取用同一公司的参与者，靠唯一约束兜底（概念架构 §4 规则 7）。
- `market_listings`：可发现性。部分唯一索引
  `uq_market_listing_active_person(person_id) WHERE status = 'active'`
  —— 同一 person 至多一条 active，重复/并发挂牌由约束收敛。
  无任何经济列（无价格/报单/结算；M1 边界，设计 D10）。

Verify: up/down/up + `alembic check`；两个部分唯一索引就位。
"""

import sqlalchemy as sa
from alembic import op

revision = "b4c6d8e0f2a3"
down_revision = "a3b5c7d9e1f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_participants",
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_market_participant_company",
        "market_participants",
        ["kind", "company_id"],
        unique=True,
        sqlite_where=sa.text("company_id IS NOT NULL"),
        postgresql_where=sa.text("company_id IS NOT NULL"),
    )

    op.create_table(
        "market_listings",
        sa.Column("person_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("quality_tier", sa.String(length=20), nullable=True),
        sa.Column("listed_by_participant_id", sa.Integer(), nullable=False),
        sa.Column("listed_at", sa.DateTime(), nullable=False),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("close_reason", sa.String(length=200), nullable=False),
        sa.Column("recruited_company_id", sa.Integer(), nullable=True),
        sa.Column("recruited_employee_id", sa.Integer(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_market_listings_status", "market_listings", ["status"])
    op.create_index(
        "uq_market_listing_active_person",
        "market_listings",
        ["person_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_market_listing_active_person", table_name="market_listings")
    op.drop_index("ix_market_listings_status", table_name="market_listings")
    op.drop_table("market_listings")
    op.drop_index("uq_market_participant_company", table_name="market_participants")
    op.drop_table("market_participants")

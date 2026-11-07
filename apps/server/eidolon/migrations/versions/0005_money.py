"""v2-0005: money_accounts / money_journal — projections of money.account.opened and
money.entry.posted (R4-b). Rebuild: `python -m eidolon rebuild money_ledger`.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "money_accounts",
        sa.Column("company_id", UUID, primary_key=True),
        sa.Column("code", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("debit_total_micro", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("credit_total_micro", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("opened_at", TS, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_table(
        "money_journal",
        sa.Column("entry_id", UUID, primary_key=True),
        sa.Column("line_no", sa.Integer(), primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("account_code", sa.String(64), nullable=False),
        sa.Column("side", sa.String(6), nullable=False),
        sa.Column("amount_micro", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("memo", sa.String(500), nullable=False),
        sa.Column("posted_at", TS, nullable=False),
        sa.Column("entry_fact_id", UUID, nullable=False),
        sa.Column("cause_fact_id", UUID, nullable=True),
        sa.Column("source_kind", sa.String(64), nullable=True),
        sa.Column("source", postgresql.JSONB(), nullable=True),
        sa.Column("run_id", UUID, nullable=True),
        sa.Column("task_id", UUID, nullable=True),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_money_journal_company_posted", "money_journal", ["company_id", "posted_at"])
    op.create_index("ix_money_journal_run", "money_journal", ["run_id"])
    op.create_index("ix_money_journal_task", "money_journal", ["task_id"])
    op.create_index("ix_money_journal_entry", "money_journal", ["entry_id"])


def downgrade() -> None:
    for name in ("entry", "task", "run", "company_posted"):
        op.drop_index(f"ix_money_journal_{name}", table_name="money_journal")
    op.drop_table("money_journal")
    op.drop_table("money_accounts")

"""v2-0001: kernel — facts, fact_chains, fact_cursors, immutability trigger.

Revision ID: 0001
Revises:
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from eidolon.kernel.store import IMMUTABILITY_DDL, IMMUTABILITY_DROP_DDL

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "facts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=True), nullable=False, unique=True),
        sa.Column("chain_seq", sa.BigInteger(), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(200)),
        sa.Column("kind", sa.String(120), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("causation_id", postgresql.UUID(as_uuid=True)),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True)),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("company_id", "idempotency_key", name="uq_facts_company_idempotency"),
        sa.UniqueConstraint("company_id", "chain_seq", name="uq_facts_company_chain_seq"),
    )
    op.create_index("ix_facts_kind_seq", "facts", ["kind", "seq"])
    op.create_index("ix_facts_correlation", "facts", ["correlation_id"])
    op.create_index("ix_facts_company_seq", "facts", ["company_id", "seq"])

    op.create_table(
        "fact_chains",
        sa.Column("company_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "fact_cursors",
        sa.Column("consumer", sa.String(120), primary_key=True),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for statement in IMMUTABILITY_DDL:
        op.execute(statement)


def downgrade() -> None:
    for statement in IMMUTABILITY_DROP_DDL:
        op.execute(statement)
    op.drop_table("fact_cursors")
    op.drop_table("fact_chains")
    op.drop_index("ix_facts_company_seq", table_name="facts")
    op.drop_index("ix_facts_correlation", table_name="facts")
    op.drop_index("ix_facts_kind_seq", table_name="facts")
    op.drop_table("facts")

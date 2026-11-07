"""v2-0002: memory_entries — projection of agent.memory.* facts (R2-d).

Rebuildable from zero: `python -m eidolon rebuild memory_entries`.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SEARCH_EXPR = "to_tsvector('english', coalesce(title, '') || ' ' || coalesce(body, ''))"


def upgrade() -> None:
    op.create_table(
        "memory_entries",
        sa.Column("memory_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", sa.String(200), nullable=False),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("department_id", sa.String(200), nullable=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("source", postgresql.JSONB(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
        sa.Column("search", postgresql.TSVECTOR(), sa.Computed(SEARCH_EXPR, persisted=True)),
    )
    op.create_index(
        "ix_memory_entries_company_person", "memory_entries", ["company_id", "person_id"]
    )
    op.create_index("ix_memory_entries_company_scope", "memory_entries", ["company_id", "scope"])
    op.create_index(
        "ix_memory_entries_search", "memory_entries", ["search"], postgresql_using="gin"
    )


def downgrade() -> None:
    op.drop_index("ix_memory_entries_search", table_name="memory_entries")
    op.drop_index("ix_memory_entries_company_scope", table_name="memory_entries")
    op.drop_index("ix_memory_entries_company_person", table_name="memory_entries")
    op.drop_table("memory_entries")

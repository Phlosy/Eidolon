"""v2-0006: talent_evidence / talent_competencies — projections of work / agent /
institution facts (R5-a). Rebuild: `python -m eidolon rebuild talent`.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "talent_evidence",
        sa.Column("fact_id", UUID, primary_key=True),
        sa.Column("person_id", sa.String(200), primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("area", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("signal", sa.SmallInteger(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("task_id", UUID, nullable=True),
        sa.Column("project_id", UUID, nullable=True),
        sa.Column("run_id", UUID, nullable=True),
        sa.Column("occurred_at", TS, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_talent_evidence_person", "talent_evidence", ["person_id", "occurred_at"])
    op.create_index("ix_talent_evidence_company_area", "talent_evidence", ["company_id", "area"])
    op.create_table(
        "talent_competencies",
        sa.Column("person_id", sa.String(200), primary_key=True),
        sa.Column("area", sa.String(64), primary_key=True),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("raw", sa.Float(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("positive", sa.Integer(), nullable=False),
        sa.Column("negative", sa.Integer(), nullable=False),
        sa.Column("last_evidence_at", TS, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("talent_competencies")
    op.drop_index("ix_talent_evidence_company_area", table_name="talent_evidence")
    op.drop_index("ix_talent_evidence_person", table_name="talent_evidence")
    op.drop_table("talent_evidence")

"""v2-0004: identity_users / identity_companies / identity_persons / identity_employments
— projections of identity.* facts (R4-a). Rebuild: `python -m eidolon rebuild identity`.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "identity_users",
        sa.Column("user_id", UUID, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_identity_users_email", "identity_users", ["email"], unique=True)
    op.create_table(
        "identity_companies",
        sa.Column("company_id", UUID, primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("owner_user_id", UUID, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_identity_companies_slug", "identity_companies", ["slug"], unique=True)
    op.create_table(
        "identity_persons",
        sa.Column("person_id", sa.String(200), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("user_id", UUID, nullable=True),
        sa.Column("persona", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_identity_persons_user", "identity_persons", ["user_id"])
    op.create_table(
        "identity_employments",
        sa.Column("employment_id", UUID, primary_key=True),
        sa.Column("company_id", UUID, nullable=False),
        sa.Column("person_id", sa.String(200), nullable=False),
        sa.Column("role", sa.String(64), nullable=False),
        sa.Column("department_id", sa.String(200), nullable=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("decision_id", UUID, nullable=True),
        sa.Column("started_at", TS, nullable=False),
        sa.Column("ended_at", TS, nullable=True),
        sa.Column("end_reason", sa.String(500), nullable=True),
        sa.Column("last_seq", sa.Integer(), nullable=False),
    )
    op.create_index("ix_identity_employments_company", "identity_employments", ["company_id", "status"])
    op.create_index("ix_identity_employments_person", "identity_employments", ["person_id"])


def downgrade() -> None:
    op.drop_index("ix_identity_employments_person", table_name="identity_employments")
    op.drop_index("ix_identity_employments_company", table_name="identity_employments")
    op.drop_table("identity_employments")
    op.drop_index("ix_identity_persons_user", table_name="identity_persons")
    op.drop_table("identity_persons")
    op.drop_index("ix_identity_companies_slug", table_name="identity_companies")
    op.drop_table("identity_companies")
    op.drop_index("ix_identity_users_email", table_name="identity_users")
    op.drop_table("identity_users")

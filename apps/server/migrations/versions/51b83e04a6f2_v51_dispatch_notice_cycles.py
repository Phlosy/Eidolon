"""v51: durable dispatch notification occurrences; no persisted readiness state."""

import sqlalchemy as sa
from alembic import op

revision = "51b83e04a6f2"
down_revision = "50a7816c2e90"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "dispatch_notices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("broadcast_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_dispatch_notices_company_id", "dispatch_notices", ["company_id"])
    op.create_index(
        "uq_dispatch_notice_open",
        "dispatch_notices",
        ["scope_type", "scope_id", "event_type"],
        unique=True,
        sqlite_where=sa.text("closed_at IS NULL"),
        postgresql_where=sa.text("closed_at IS NULL"),
    )
    op.create_index(
        "ix_dispatch_notice_pending",
        "dispatch_notices",
        ["id"],
        sqlite_where=sa.text("closed_at IS NULL AND broadcast_at IS NULL"),
        postgresql_where=sa.text("closed_at IS NULL AND broadcast_at IS NULL"),
    )


def downgrade():
    op.drop_index("ix_dispatch_notice_pending", table_name="dispatch_notices")
    op.drop_index("uq_dispatch_notice_open", table_name="dispatch_notices")
    op.drop_index("ix_dispatch_notices_company_id", table_name="dispatch_notices")
    op.drop_table("dispatch_notices")

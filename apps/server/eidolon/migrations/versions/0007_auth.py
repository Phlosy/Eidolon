"""v2-0007: auth_credentials / auth_sessions — the API layer's own persistent
state (R5-b, v2-ADR-22): password hashes and sessions are secrets, neither facts
nor projections. Not rebuildable; losing them means re-login / password reset.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "auth_credentials",
        sa.Column("user_id", UUID, primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(500), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_index("ix_auth_credentials_email", "auth_credentials", ["email"], unique=True)
    op.create_table(
        "auth_sessions",
        sa.Column("session_id", UUID, primary_key=True),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.Column("revoked_at", TS, nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=False),
    )
    op.create_index("ix_auth_sessions_token", "auth_sessions", ["token_hash"], unique=True)
    op.create_index("ix_auth_sessions_user", "auth_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_user", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_token", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_index("ix_auth_credentials_email", table_name="auth_credentials")
    op.drop_table("auth_credentials")

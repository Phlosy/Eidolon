"""v2-0008: provider_credentials / auth_passkeys / auth_webauthn_challenges — API-layer
secrets (R6-d, v2-ADR-22): Fernet-encrypted provider keys per company, WebAuthn
credentials and single-use ceremony challenges. Not rebuildable; losing them means
re-entering a key or re-registering a passkey.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "provider_credentials",
        sa.Column("company_id", UUID, primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("api_key_encrypted", sa.String(2000), nullable=False),
        sa.Column("key_tail", sa.String(8), nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("updated_by", sa.String(200), nullable=False),
    )
    op.create_table(
        "auth_passkeys",
        sa.Column("credential_id", sa.String(1024), primary_key=True),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("public_key", sa.String(2048), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("transports", postgresql.JSONB(), nullable=False),
        sa.Column("device_type", sa.String(32), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_used_at", TS, nullable=True),
    )
    op.create_index("ix_auth_passkeys_user_id", "auth_passkeys", ["user_id"])
    op.create_table(
        "auth_webauthn_challenges",
        sa.Column("challenge", sa.String(256), primary_key=True),
        sa.Column("user_id", UUID, nullable=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("name", sa.String(120), nullable=True),
        sa.Column("expires_at", TS, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("auth_webauthn_challenges")
    op.drop_index("ix_auth_passkeys_user_id", table_name="auth_passkeys")
    op.drop_table("auth_passkeys")
    op.drop_table("provider_credentials")

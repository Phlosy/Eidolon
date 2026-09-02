"""v0.7 human authentication, memberships and user tutorial progress.

Revision ID: e7a1c9b5d304
Revises: c3a7e9f1b204
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1c9b5d304"
down_revision: str | Sequence[str] | None = "c3a7e9f1b204"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    ]


def upgrade() -> None:
    op.add_column(
        "companies", sa.Column("stage", sa.String(30), nullable=False, server_default="FOUNDING")
    )
    # Preserve the lifecycle of installations that already contain an operating
    # company. Only companies created by the new registration flow start empty.
    op.execute(
        "UPDATE companies SET stage = 'OPERATING' "
        "WHERE id IN (SELECT DISTINCT company_id FROM employees)"
    )
    with op.batch_alter_table("drive_nodes") as batch:
        batch.add_column(sa.Column("company_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_drive_nodes_company", "companies", ["company_id"], ["id"])
        batch.create_index("ix_drive_nodes_company_id", ["company_id"])
    op.execute(
        "UPDATE drive_nodes SET company_id = "
        "COALESCE((SELECT company_id FROM projects WHERE projects.id = drive_nodes.project_id), "
        "(SELECT company_id FROM employees WHERE employees.id = drive_nodes.owner_employee_id), "
        "(SELECT id FROM companies ORDER BY id LIMIT 1))"
    )
    for table, constraint in [
        ("providers", "fk_providers_company"),
        ("git_connections", "fk_git_connections_company"),
    ]:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("company_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(constraint, "companies", ["company_id"], ["id"])
            batch.create_index(f"ix_{table}_company_id", ["company_id"])
        op.execute(
            f"UPDATE {table} SET company_id = (SELECT id FROM companies ORDER BY id LIMIT 1)"
        )
    op.create_table(
        "users",
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("email_verified", sa.Boolean(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("avatar", sa.String(500), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("onboarding_status", sa.String(30), nullable=False),
        sa.Column("locale", sa.String(20), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_table(
        "pending_registrations",
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("locale", sa.String(20), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("email"),
    )
    op.create_index(
        "ix_pending_registrations_email", "pending_registrations", ["email"], unique=True
    )
    op.create_index("ix_pending_registrations_expires_at", "pending_registrations", ["expires_at"])
    op.create_table(
        "email_verification_tokens",
        sa.Column("pending_registration_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["pending_registration_id"], ["pending_registrations.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index(
        "ix_email_verification_tokens_pending_registration_id",
        "email_verification_tokens",
        ["pending_registration_id"],
    )
    op.create_index(
        "ix_email_verification_tokens_token_hash",
        "email_verification_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_email_verification_tokens_expires_at", "email_verification_tokens", ["expires_at"]
    )
    op.create_table(
        "company_memberships",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "company_id"),
    )
    op.create_index("ix_company_memberships_user_id", "company_memberships", ["user_id"])
    op.create_index("ix_company_memberships_company_id", "company_memberships", ["company_id"])
    op.create_table(
        "user_sessions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_token", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("ip_address", sa.String(100), nullable=False),
        sa.Column("user_agent", sa.String(500), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"], unique=True)
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.create_table(
        "passkey_credentials",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.Text(), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("transports", sa.JSON(), nullable=False),
        sa.Column("device_type", sa.String(50), nullable=False),
        sa.Column("backed_up", sa.Boolean(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("credential_id"),
    )
    op.create_index("ix_passkey_credentials_user_id", "passkey_credentials", ["user_id"])
    op.create_table(
        "webauthn_challenges",
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("ceremony", sa.String(30), nullable=False),
        sa.Column("challenge", sa.Text(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index("ix_webauthn_challenges_user_id", "webauthn_challenges", ["user_id"])
    op.create_index("ix_webauthn_challenges_ceremony", "webauthn_challenges", ["ceremony"])
    op.create_index("ix_webauthn_challenges_expires_at", "webauthn_challenges", ["expires_at"])
    op.create_table(
        "user_audit_events",
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("company_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("ip_address", sa.String(100), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
    )
    op.create_index("ix_user_audit_events_user_id", "user_audit_events", ["user_id"])
    op.create_index("ix_user_audit_events_company_id", "user_audit_events", ["company_id"])
    op.create_index("ix_user_audit_events_action", "user_audit_events", ["action"])
    op.create_table(
        "user_tutorial_progress",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("tutorial_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("current_stage", sa.String(80), nullable=False),
        sa.Column("current_step", sa.String(80), nullable=False),
        sa.Column("completed_steps", sa.JSON(), nullable=False),
        sa.Column("skipped_steps", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("paused_at", sa.DateTime(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.UniqueConstraint("user_id", "tutorial_id"),
    )
    op.create_index("ix_user_tutorial_progress_user_id", "user_tutorial_progress", ["user_id"])
    op.create_index(
        "ix_user_tutorial_progress_company_id", "user_tutorial_progress", ["company_id"]
    )
    with op.batch_alter_table("review_meetings") as batch:
        batch.add_column(sa.Column("acted_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_review_meetings_acted_by_user",
            "users",
            ["acted_by_user_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    def has_column(table: str, column: str) -> bool:
        return column in {item["name"] for item in sa.inspect(bind).get_columns(table)}

    if has_column("review_meetings", "acted_by_user_id"):
        with op.batch_alter_table("review_meetings") as batch:
            batch.drop_constraint("fk_review_meetings_acted_by_user", type_="foreignkey")
            batch.drop_column("acted_by_user_id")
    for table in [
        "user_tutorial_progress",
        "user_audit_events",
        "webauthn_challenges",
        "passkey_credentials",
        "user_sessions",
        "company_memberships",
        "email_verification_tokens",
        "pending_registrations",
        "users",
    ]:
        if sa.inspect(bind).has_table(table):
            op.drop_table(table)
    if has_column("drive_nodes", "company_id"):
        with op.batch_alter_table("drive_nodes") as batch:
            batch.drop_index("ix_drive_nodes_company_id")
            batch.drop_constraint("fk_drive_nodes_company", type_="foreignkey")
            batch.drop_column("company_id")
    for table, constraint in [
        ("providers", "fk_providers_company"),
        ("git_connections", "fk_git_connections_company"),
    ]:
        if has_column(table, "company_id"):
            with op.batch_alter_table(table) as batch:
                batch.drop_index(f"ix_{table}_company_id")
                batch.drop_constraint(constraint, type_="foreignkey")
                batch.drop_column("company_id")
    if has_column("companies", "stage"):
        op.drop_column("companies", "stage")

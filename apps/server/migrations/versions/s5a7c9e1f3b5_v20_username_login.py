"""v20: users.username + pending_registrations.username（用户名/邮箱双登录）

Revision ID: s5a7c9e1f3b5
Revises: r4a6b8c0d2e4
Create Date: 2026-01-01

既有用户回填 username = email local part 清洗（冲突加 -n）；此后登录支持
username 或 email。
"""

import sqlalchemy as sa

from alembic import op

revision = "s5a7c9e1f3b5"
down_revision = "r4a6b8c0d2e4"
branch_labels = None
depends_on = None


def _derive(email: str, taken: set[str]) -> str:
    base = "".join(c if c.isalnum() or c in "-_" else "-" for c in email.split("@")[0].lower())
    base = base.strip("-") or "user"
    username = base
    n = 2
    while username in taken:
        username = f"{base}-{n}"
        n += 1
    taken.add(username)
    return username


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(100), nullable=True))
    conn = op.get_bind()
    taken: set[str] = set()
    rows = conn.execute(sa.text("SELECT id, email FROM users ORDER BY id")).fetchall()
    for uid, email in rows:
        conn.execute(
            sa.text("UPDATE users SET username = :username WHERE id = :uid"),
            {"username": _derive(str(email), taken), "uid": uid},
        )
    op.alter_column("users", "username", existing_type=sa.String(100), nullable=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.add_column(
        "pending_registrations",
        sa.Column("username", sa.String(100), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
    op.drop_column("pending_registrations", "username")

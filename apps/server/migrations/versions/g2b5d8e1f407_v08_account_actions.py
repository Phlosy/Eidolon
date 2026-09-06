"""v0.8 account action confirmation tokens (email change / password change / deletion).

Revision ID: g2b5d8e1f407
Revises: e7a1c9b5d304
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g2b5d8e1f407"
down_revision: str | Sequence[str] | None = "e7a1c9b5d304"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "account_action_tokens"
INDEXES = (
    ("ix_account_action_tokens_user_id", ["user_id"], False),
    ("ix_account_action_tokens_action", ["action"], False),
    ("ix_account_action_tokens_token_hash", ["token_hash"], True),
    ("ix_account_action_tokens_expires_at", ["expires_at"], False),
)


def upgrade() -> None:
    # 历史库兼容：本 revision 之前的启动路径是 create_all（现已由 Alembic 全权接管，
    # 见 app/core/database.py:ensure_database_schema），所以先跑到过 v0.8 模型的 dev 库里
    # 可能已经有这张表（列与索引定义一致）。无条件 create_table 会抛
    # "table already exists" 并卡死后续 revision，故逐项判存。
    # 全新库走 alembic upgrade head，这里永远命不中。
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(40), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.Column("used_at", sa.DateTime(), nullable=True),
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        )
        existing: set[str] = set()
    else:
        existing = {item["name"] for item in sa.inspect(bind).get_indexes(TABLE)}
    for name, columns, unique in INDEXES:
        if name not in existing:
            op.create_index(name, TABLE, columns, unique=unique)


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_table(TABLE)

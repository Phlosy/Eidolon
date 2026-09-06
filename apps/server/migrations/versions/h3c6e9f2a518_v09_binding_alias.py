"""v0.9 model binding aliases (display name vs. real remote model name).

Revision ID: h3c6e9f2a518
Revises: g2b5d8e1f407
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "h3c6e9f2a518"
down_revision: str | Sequence[str] | None = "g2b5d8e1f407"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if "alias" in {item["name"] for item in sa.inspect(bind).get_columns("model_bindings")}:
        return  # 已经补过，或该库是在模型已有 alias 后由旧版 create_all 建的
    with op.batch_alter_table("model_bindings") as batch:
        batch.add_column(sa.Column("alias", sa.String(200), nullable=False, server_default=""))


def downgrade() -> None:
    with op.batch_alter_table("model_bindings") as batch:
        batch.drop_column("alias")

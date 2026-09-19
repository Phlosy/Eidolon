"""v48: reserve a unique loopback control port per runtime instance."""

import sqlalchemy as sa
from alembic import op

revision: str = "48e38b2049c1"
down_revision: str = "47d50a3618ef"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_runtime_loopback_port",
        "runtime_instances",
        ["internal_port"],
        unique=True,
        sqlite_where=sa.text("internal_host = '127.0.0.1'"),
        postgresql_where=sa.text("internal_host = '127.0.0.1'"),
    )


def downgrade() -> None:
    op.drop_index("uq_runtime_loopback_port", table_name="runtime_instances")

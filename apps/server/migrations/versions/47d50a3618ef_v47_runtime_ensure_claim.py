"""v47: stable runtime resource identity and per-instance provisioning claims.

Existing rows stay unknown until ensure inspects their recorded resources.
No container or workspace is created, adopted or deleted by the migration.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "47d50a3618ef"
down_revision: str = "46c2d8109a7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runtime_instances", sa.Column("resource_key", sa.String(36), nullable=True))
    op.add_column(
        "runtime_instances", sa.Column("provisioning_token", sa.String(36), nullable=True)
    )
    op.add_column(
        "runtime_instances", sa.Column("provisioning_claimed_at", sa.DateTime(), nullable=True)
    )
    op.create_index(
        "uq_runtime_instances_resource_key", "runtime_instances", ["resource_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("uq_runtime_instances_resource_key", table_name="runtime_instances")
    for column in ("provisioning_claimed_at", "provisioning_token", "resource_key"):
        op.drop_column("runtime_instances", column)

"""v46: durable runtime provisioning intent ownership and step receipts.

Existing readiness jobs are identified only by their persisted metadata source.
Other historical jobs retain the lifecycle owner; unknown source stays legacy.
No job is executed, deleted or marked successful by this migration.
"""

import json

import sqlalchemy as sa
from alembic import op

revision: str = "46c2d8109a7b"
down_revision: str = "325887b7109a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "provisioning_jobs",
        sa.Column("owner", sa.String(50), nullable=False, server_default="lifecycle"),
    )
    op.add_column(
        "provisioning_jobs",
        sa.Column("source", sa.String(100), nullable=False, server_default="app.lifecycle.engine"),
    )
    op.add_column("provisioning_jobs", sa.Column("claim_token", sa.String(36), nullable=True))
    op.add_column("provisioning_jobs", sa.Column("claimed_at", sa.DateTime(), nullable=True))
    op.add_column(
        "provisioning_steps",
        sa.Column("result_json", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_provisioning_jobs_owner_status", "provisioning_jobs", ["owner", "status"])
    connection = op.get_bind()
    updates = []
    update_stmt = sa.text("UPDATE provisioning_jobs SET owner=:owner, source=:source WHERE id=:id")
    for row in connection.execute(sa.text("SELECT id, metadata_json FROM provisioning_jobs")):
        metadata = row.metadata_json
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        source = metadata.get("source") if isinstance(metadata, dict) else None
        source = source if isinstance(source, str) and source else "legacy"
        requested_by = metadata.get("requested_by") if isinstance(metadata, dict) else None
        updates.append(
            {
                "id": row.id,
                "source": requested_by
                if isinstance(requested_by, str) and requested_by
                else source,
                "owner": "runtime" if source == "app.work.readiness" else "lifecycle",
            },
        )
        if len(updates) == 500:
            connection.execute(update_stmt, updates)
            updates.clear()
    if updates:
        connection.execute(update_stmt, updates)


def downgrade() -> None:
    op.drop_index("ix_provisioning_jobs_owner_status", table_name="provisioning_jobs")
    op.drop_column("provisioning_steps", "result_json")
    for column in ("claimed_at", "claim_token", "source", "owner"):
        op.drop_column("provisioning_jobs", column)

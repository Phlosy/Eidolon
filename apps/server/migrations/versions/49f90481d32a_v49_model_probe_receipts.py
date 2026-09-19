"""v49: explicit, bounded selected-model validation receipts."""

import sqlalchemy as sa
from alembic import op

revision = "49f90481d32a"
down_revision = "48e38b2049c1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runtime_model_probes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=False),
        sa.Column("runtime_instance_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "runtime_instance_id", "idempotency_key", name="uq_runtime_model_probe_key"
        ),
    )
    op.create_index(
        "uq_runtime_model_probe_running",
        "runtime_model_probes",
        ["runtime_instance_id"],
        unique=True,
        sqlite_where=sa.text("state = 'running'"),
        postgresql_where=sa.text("state = 'running'"),
    )


def downgrade():
    op.drop_index("uq_runtime_model_probe_running", table_name="runtime_model_probes")
    op.drop_table("runtime_model_probes")

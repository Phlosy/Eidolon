"""v50: capture execution history; old unknowns are deliberately not backfilled."""

import sqlalchemy as sa
from alembic import op

revision = "50a7816c2e90"
down_revision = "49f90481d32a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("work_sessions", sa.Column("person_id", sa.Integer(), nullable=True))
    op.add_column("work_sessions", sa.Column("company_id", sa.Integer(), nullable=True))
    op.add_column("work_sessions", sa.Column("deployment_mode", sa.String(50), nullable=True))
    op.add_column("work_sessions", sa.Column("provenance_snapshot_json", sa.JSON(), nullable=True))
    op.create_index("ix_work_sessions_person_id", "work_sessions", ["person_id"])
    op.create_index("ix_work_sessions_company_id", "work_sessions", ["company_id"])
    # Kept local to this revision so later application changes cannot change an
    # existing migration. Fresh metadata schemas install the same SQLite guards.
    columns = (
        "task_id",
        "employee_id",
        "person_id",
        "company_id",
        "runtime_type",
        "runtime_instance_id",
        "provider_id",
        "model",
        "deployment_mode",
        "provenance_snapshot_json",
    )
    changed = " OR ".join(f"NEW.{field} IS NOT OLD.{field}" for field in columns)
    for name, condition in (
        ("work_session_provenance_immutable", changed),
        (
            "work_session_runtime_ref_once",
            "NEW.runtime_session_ref IS NOT OLD.runtime_session_ref AND "
            "(OLD.ended_at IS NOT NULL OR COALESCE(OLD.runtime_session_ref, '') <> '')",
        ),
        (
            "work_session_ended_once",
            "OLD.ended_at IS NOT NULL AND NEW.ended_at IS NOT OLD.ended_at",
        ),
    ):
        op.execute(
            f"CREATE TRIGGER {name} BEFORE UPDATE ON work_sessions WHEN {condition} "
            "BEGIN SELECT RAISE(ABORT, 'work session provenance is immutable'); END"
        )


def downgrade():
    for name in (
        "work_session_provenance_immutable",
        "work_session_runtime_ref_once",
        "work_session_ended_once",
    ):
        op.execute(f"DROP TRIGGER {name}")
    op.drop_index("ix_work_sessions_company_id", table_name="work_sessions")
    op.drop_index("ix_work_sessions_person_id", table_name="work_sessions")
    for column in ("provenance_snapshot_json", "deployment_mode", "company_id", "person_id"):
        op.drop_column("work_sessions", column)

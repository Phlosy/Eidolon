"""v53: explicit WorkSession purposes without fabricated worker tasks or history."""

import sqlalchemy as sa
from alembic import op

revision = "53d82fa710bc"
down_revision = "52c94fd1a807"
branch_labels = None
depends_on = None


# Revision-local copies: future model edits must not alter this migration.
_PURPOSE_TARGET = (
    "(purpose = 'worker' AND task_id IS NOT NULL AND project_id IS NULL "
    "AND review_request_id IS NULL) OR "
    "(purpose = 'management' AND task_id IS NULL AND project_id IS NOT NULL "
    "AND review_request_id IS NULL) OR "
    "(purpose = 'review' AND task_id IS NULL AND project_id IS NOT NULL "
    "AND review_request_id IS NOT NULL)"
)
_INVALID_TARGET = (
    "(NEW.purpose = 'management' AND NOT EXISTS ("
    "SELECT 1 FROM projects p JOIN companies c ON c.id = p.company_id "
    "JOIN employees e ON e.id = NEW.employee_id "
    "WHERE p.id = NEW.project_id AND p.company_id = NEW.company_id "
    "AND e.company_id = p.company_id AND e.lifecycle_status = 'active' "
    "AND p.management_employee_id = e.id)) OR "
    "(NEW.purpose = 'review' AND NOT EXISTS ("
    "SELECT 1 FROM review_requests r JOIN projects p ON p.id = r.project_id "
    "JOIN companies c ON c.id = p.company_id JOIN tasks t ON t.id = r.task_id "
    "JOIN employees e ON e.id = NEW.employee_id "
    "WHERE r.id = NEW.review_request_id AND p.id = NEW.project_id "
    "AND t.project_id = p.id AND r.company_id = p.company_id "
    "AND NEW.company_id = p.company_id AND e.company_id = p.company_id "
    "AND e.lifecycle_status = 'active' AND r.reviewer_employee_id = e.id))"
)
_V50_COLUMNS = (
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


def _require_rebuild_connection():
    # SQLite cannot turn FK enforcement off inside an active transaction. The
    # application and historical migrations use OFF; refuse other connections
    # before DDL instead of risking inbound session references on DROP TABLE.
    if op.get_bind().exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError(
            "v53 requires a dedicated SQLite migration connection with foreign_keys=OFF"
        )


def _restore_provenance_guards(*, purposes):
    columns = (("purpose", "project_id", "review_request_id") if purposes else ()) + _V50_COLUMNS
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


def upgrade():
    _require_rebuild_connection()
    # Rebuilding preserves IDs/values and inbound references, but SQLite drops
    # this table's triggers. Restore all v50 guards immediately after the copy.
    with op.batch_alter_table("work_sessions") as batch:
        batch.alter_column("task_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(
            sa.Column("purpose", sa.String(20), nullable=False, server_default="worker")
        )
        batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("review_request_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_work_sessions_project", "projects", ["project_id"], ["id"])
        batch.create_foreign_key(
            "fk_work_sessions_review_request", "review_requests", ["review_request_id"], ["id"]
        )
        batch.create_check_constraint("ck_work_session_purpose_target", _PURPOSE_TARGET)
        batch.create_index("ix_work_sessions_project_id", ["project_id"])
        batch.create_index("ix_work_sessions_review_request_id", ["review_request_id"])
    _restore_provenance_guards(purposes=True)
    op.execute(
        "CREATE TRIGGER work_session_target_valid BEFORE INSERT ON work_sessions "
        f"WHEN {_INVALID_TARGET} "
        "BEGIN SELECT RAISE(ABORT, 'invalid work session target'); END"
    )


def downgrade():
    # Check before dropping any trigger, index, constraint, column or table.
    # There is no truthful v52 representation of a project/review-only session.
    if op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM work_sessions WHERE purpose <> 'worker'")
    ):
        raise RuntimeError("cannot downgrade v53 while non-worker work sessions exist")
    _require_rebuild_connection()
    with op.batch_alter_table("work_sessions") as batch:
        batch.drop_index("ix_work_sessions_review_request_id")
        batch.drop_index("ix_work_sessions_project_id")
        batch.drop_constraint("ck_work_session_purpose_target", type_="check")
        batch.drop_constraint("fk_work_sessions_review_request", type_="foreignkey")
        batch.drop_constraint("fk_work_sessions_project", type_="foreignkey")
        batch.drop_column("review_request_id")
        batch.drop_column("project_id")
        batch.drop_column("purpose")
        batch.alter_column("task_id", existing_type=sa.Integer(), nullable=False)
    _restore_provenance_guards(purposes=False)

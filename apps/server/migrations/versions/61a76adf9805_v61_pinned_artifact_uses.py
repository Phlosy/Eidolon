"""v61: pin canonical revisions and append each worker session's artifact use."""

import sqlalchemy as sa
from alembic import op

revision = "61a76adf9805"
down_revision = "60f659ce8704"
branch_labels = None
depends_on = None

_VALID_REVISION = (
    "EXISTS (SELECT 1 FROM drive_revisions r JOIN drive_nodes n ON n.id=r.node_id "
    "JOIN work_sessions producer ON producer.id=n.work_session_id "
    "JOIN runtime_attempts a ON a.work_session_id=producer.id "
    "JOIN runtime_artifact_collections c ON c.attempt_id=a.id, json_each(c.entries_json) e "
    "JOIN tasks t ON t.id=NEW.task_id JOIN projects p ON p.id=t.project_id "
    "WHERE r.id=NEW.revision_id AND n.id=NEW.artifact_id AND r.content_path IS NOT NULL "
    "AND json_extract(e.value, '$.revision_id')=r.id "
    "AND n.project_id=t.project_id AND n.company_id=p.company_id AND n.task_id<>t.id "
    "AND NEW.role='consumed_by' AND (NEW.work_session_id IS NULL OR EXISTS ("
    "SELECT 1 FROM work_sessions w WHERE w.id=NEW.work_session_id AND w.task_id=t.id "
    "AND w.purpose='worker' AND w.company_id=p.company_id "
    "AND w.employee_id=NEW.actor_employee_id AND w.status='running' AND t.status='in_progress')))"
)
LINK_GUARDS = (
    (
        "artifact_links",
        "artifact_link_valid_revision",
        "INSERT",
        f"NEW.revision_id IS NOT NULL AND NOT ({_VALID_REVISION})",
    ),
    (
        "artifact_links",
        "artifact_link_immutable",
        "UPDATE",
        "OLD.revision_id IS NOT NULL OR NEW.revision_id IS NOT NULL",
    ),
    ("artifact_links", "artifact_link_no_delete", "DELETE", "OLD.revision_id IS NOT NULL"),
    (
        "artifact_links",
        "artifact_link_no_replace",
        "INSERT",
        "EXISTS (SELECT 1 FROM artifact_links l WHERE "
        "(l.revision_id IS NOT NULL OR NEW.revision_id IS NOT NULL) AND "
        "(l.id=NEW.id OR (l.artifact_id=NEW.artifact_id AND l.task_id=NEW.task_id "
        "AND l.role=NEW.role AND l.work_session_id IS NEW.work_session_id)))",
    ),
    (
        "work_sessions",
        "artifact_link_worker_no_replace",
        "INSERT",
        "EXISTS (SELECT 1 FROM artifact_links l WHERE l.work_session_id=NEW.id "
        "AND l.revision_id IS NOT NULL)",
    ),
    (
        "work_sessions",
        "artifact_link_worker_no_delete",
        "DELETE",
        "EXISTS (SELECT 1 FROM artifact_links l WHERE l.work_session_id=OLD.id "
        "AND l.revision_id IS NOT NULL)",
    ),
)


def _sqlite_transaction():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        raw = connection.connection.driver_connection
        if not raw.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade():
    _sqlite_transaction()
    with op.batch_alter_table("artifact_links") as batch:
        batch.add_column(sa.Column("revision_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_artifact_links_revision_id", "drive_revisions", ["revision_id"], ["id"]
        )
        batch.drop_constraint("uq_artifact_links_artifact_task_role", type_="unique")
        batch.create_index(
            "uq_artifact_links_declaration",
            ["artifact_id", "task_id", "role"],
            unique=True,
            sqlite_where=sa.text("work_session_id IS NULL"),
        )
        batch.create_index(
            "uq_artifact_links_use",
            ["artifact_id", "task_id", "role", "work_session_id"],
            unique=True,
            sqlite_where=sa.text("work_session_id IS NOT NULL"),
        )
    if op.get_bind().dialect.name == "sqlite":
        for table, name, operation, condition in LINK_GUARDS:
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON {table} WHEN {condition} "
                "BEGIN SELECT RAISE(ABORT, 'invalid or immutable artifact link'); END"
            )


def downgrade():
    _sqlite_transaction()
    connection = op.get_bind()
    pinned = connection.exec_driver_sql(
        "SELECT 1 FROM artifact_links WHERE revision_id IS NOT NULL LIMIT 1"
    ).scalar()
    repeated = connection.exec_driver_sql(
        "SELECT 1 FROM artifact_links GROUP BY artifact_id,task_id,role HAVING COUNT(*)>1 LIMIT 1"
    ).scalar()
    if pinned or repeated:
        raise RuntimeError("v61 downgrade would erase artifact use history")
    if connection.dialect.name == "sqlite":
        for _, name, _, _ in LINK_GUARDS:
            op.execute(f"DROP TRIGGER {name}")
    with op.batch_alter_table("artifact_links") as batch:
        batch.drop_index("uq_artifact_links_declaration")
        batch.drop_index("uq_artifact_links_use")
        batch.drop_constraint("fk_artifact_links_revision_id", type_="foreignkey")
        batch.drop_column("revision_id")
        batch.create_unique_constraint(
            "uq_artifact_links_artifact_task_role", ["artifact_id", "task_id", "role"]
        )

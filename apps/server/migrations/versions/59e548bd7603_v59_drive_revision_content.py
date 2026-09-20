"""v59: preserve exact canonical revision bytes without fabricating legacy content."""

import sqlalchemy as sa
from alembic import op

revision = "59e548bd7603"
down_revision = "58d437ac6502"
branch_labels = None
depends_on = None

_CONTENT_IDENTITY = (
    "id",
    "node_id",
    "version",
    "sha256",
    "author_employee_id",
    "author_person_id",
    "message",
    "created_at",
    "content_path",
)
_VERSION_CONFLICT = (
    "EXISTS (SELECT 1 FROM drive_revisions r WHERE r.node_id=NEW.node_id "
    "AND r.version=NEW.version AND r.id <> NEW.id "
    "AND (r.content_path IS NOT NULL OR NEW.content_path IS NOT NULL))"
)
CONTENT_GUARDS = (
    (
        "drive_revision_content_no_replace",
        "INSERT",
        "EXISTS (SELECT 1 FROM drive_revisions r WHERE r.id=NEW.id AND r.content_path IS NOT NULL)",
    ),
    (
        "drive_revision_content_valid",
        "INSERT",
        "NEW.content_path IS NOT NULL AND (NEW.node_id < 1 OR NEW.version < 1 OR "
        "length(NEW.sha256) <> 64 OR NEW.sha256 GLOB '*[^0-9a-f]*' OR "
        "NEW.content_path IS NOT ('drive/.revisions/' || NEW.node_id || '/' || NEW.version "
        "|| '-' || NEW.sha256 || '.txt') OR NOT EXISTS "
        "(SELECT 1 FROM drive_nodes n WHERE n.id=NEW.node_id AND n.kind='document'))",
    ),
    (
        "drive_revision_content_immutable",
        "UPDATE",
        "(OLD.content_path IS NOT NULL OR NEW.content_path IS NOT NULL) AND ("
        + " OR ".join(f"NEW.{column} IS NOT OLD.{column}" for column in _CONTENT_IDENTITY)
        + ")",
    ),
    ("drive_revision_content_no_delete", "DELETE", "OLD.content_path IS NOT NULL"),
    ("drive_revision_version_insert_valid", "INSERT", _VERSION_CONFLICT),
    ("drive_revision_version_update_valid", "UPDATE", _VERSION_CONFLICT),
)


def _sqlite_transaction():
    connection = op.get_bind()
    if (
        connection.dialect.name == "sqlite"
        and not connection.connection.driver_connection.in_transaction
    ):
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def upgrade():
    _sqlite_transaction()
    op.add_column("drive_revisions", sa.Column("content_path", sa.String(500), nullable=True))
    if op.get_bind().dialect.name == "sqlite":
        for name, operation, condition in CONTENT_GUARDS:
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {operation} ON drive_revisions WHEN {condition} "
                "BEGIN SELECT RAISE(ABORT, 'invalid or immutable revision content'); END"
            )


def downgrade():
    _sqlite_transaction()
    if (
        op.get_bind()
        .exec_driver_sql("SELECT COUNT(*) FROM drive_revisions WHERE content_path IS NOT NULL")
        .scalar()
    ):
        raise RuntimeError("v59 downgrade would erase preserved revision content")
    if op.get_bind().dialect.name == "sqlite":
        for name, _, _ in CONTENT_GUARDS:
            op.execute(f"DROP TRIGGER {name}")
    op.drop_column("drive_revisions", "content_path")

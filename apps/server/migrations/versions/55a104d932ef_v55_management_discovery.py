"""v55: bounded, durable progress for management source discovery."""

import sqlalchemy as sa
from alembic import op

revision = "55a104d932ef"
down_revision = "54e93ab821cd"
branch_labels = None
depends_on = None

# Revision-local source vocabulary, independent of future application code.
SOURCE_TYPES = (
    "project.replan_required",
    "task.assignment_required",
    "task.blocked",
    "task.failed",
    "task.review_failed",
    "task.review_named",
    "task.review_required",
    "task.runtime_unavailable",
)


def upgrade():
    op.create_table(
        "management_discovery_cursors",
        sa.Column("scope_company_id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
        sa.CheckConstraint("scope_company_id >= 0", name="ck_management_discovery_scope"),
        sa.CheckConstraint("last_event_id >= 0", name="ck_management_discovery_event"),
    )
    op.create_index("ix_events_management_discovery", "events", ["company_id", "type", "id"])
    if op.get_bind().dialect.name == "sqlite":
        for suffix, operation in (
            ("insert", "INSERT"),
            ("update", "UPDATE OF id, type, company_id"),
        ):
            op.execute(
                f"CREATE TRIGGER management_discovery_rewind_{suffix} AFTER {operation} ON events "
                "WHEN NEW.id > 0 AND NEW.type IN ("
                + ", ".join(repr(value) for value in SOURCE_TYPES)
                + ") BEGIN UPDATE management_discovery_cursors SET last_event_id = NEW.id - 1 "
                "WHERE last_event_id >= NEW.id AND "
                "(scope_company_id = 0 OR scope_company_id = NEW.company_id); END"
            )


def downgrade():
    # Progress is a cache: the v54 scanner safely rediscovers retained dedup facts.
    if op.get_bind().dialect.name == "sqlite":
        for suffix in ("insert", "update"):
            op.execute(f"DROP TRIGGER management_discovery_rewind_{suffix}")
    op.drop_index("ix_events_management_discovery", table_name="events")
    op.drop_table("management_discovery_cursors")

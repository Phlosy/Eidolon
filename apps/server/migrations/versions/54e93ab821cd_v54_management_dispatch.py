"""v54: durable management source observations and once-only execution links."""

import sqlalchemy as sa
from alembic import op

revision = "54e93ab821cd"
down_revision = "53d82fa710bc"
branch_labels = None
depends_on = None

# Frozen SQL guards: future models cannot rewrite this revision.
_IDENTITY_FIELDS = (
    "id",
    "created_at",
    "source_event_id",
    "source_type",
    "purpose",
    "company_id",
    "project_id",
    "task_id",
    "review_request_id",
)
_IDENTITY_CHANGED = " OR ".join(f"NEW.{name} IS NOT OLD.{name}" for name in _IDENTITY_FIELDS)
# Guards deliberately also apply with PRAGMA foreign_keys=OFF, as in production.
_INVALID_SOURCE = (
    "NOT EXISTS (SELECT 1 FROM events e WHERE e.id = NEW.source_event_id AND e.type = "
    "NEW.source_type) OR "
    "(NEW.company_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM events e JOIN companies c "
    "ON c.id=e.company_id "
    "WHERE e.id=NEW.source_event_id AND c.id=NEW.company_id)) OR "
    "(NEW.project_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM events e JOIN projects p ON "
    "p.id=e.project_id "
    "WHERE e.id=NEW.source_event_id AND p.id=NEW.project_id AND p.company_id=NEW.company_id)) OR "
    "(NEW.task_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM events e JOIN tasks t ON "
    "t.id=e.task_id "
    "WHERE e.id=NEW.source_event_id AND t.id=NEW.task_id AND t.project_id=NEW.project_id)) OR "
    "(NEW.review_request_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM review_requests r "
    "JOIN events e "
    "ON e.id=NEW.source_event_id WHERE r.id=NEW.review_request_id AND r.company_id=NEW.company_id "
    "AND r.project_id=NEW.project_id AND r.task_id=NEW.task_id AND json_valid(e.payload) "
    "AND json_type(e.payload, '$.review_request_id')='integer' "
    "AND json_extract(e.payload, '$.review_request_id')=r.id)) OR "
    "(COALESCE(NEW.resolution, '') <> 'invalid' AND (NEW.company_id IS NULL OR "
    "NEW.project_id IS NULL OR "
    "(substr(NEW.source_type, 1, 5)='task.' AND NEW.task_id IS NULL) OR "
    "(NEW.purpose='review' AND NEW.review_request_id IS NULL) OR "
    "NOT EXISTS (SELECT 1 FROM events e WHERE e.id=NEW.source_event_id AND e.task_id IS "
    "NEW.task_id)))"
)
_INVALID_RECIPIENT = (
    "NEW.recipient_employee_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM employees e "
    "WHERE e.id=NEW.recipient_employee_id AND e.company_id=NEW.company_id)"
)
_INVALID_LINK = (
    "NEW.work_session_id IS NOT NULL AND (" + _INVALID_SOURCE + " OR NOT EXISTS ("
    "SELECT 1 FROM work_sessions w JOIN employees e ON e.id=w.employee_id JOIN persons n "
    "ON n.id=e.person_id "
    "JOIN projects p ON p.id=NEW.project_id WHERE w.id=NEW.work_session_id "
    "AND w.purpose=NEW.purpose AND w.task_id IS NULL AND w.project_id=NEW.project_id "
    "AND w.review_request_id IS NEW.review_request_id AND w.company_id=NEW.company_id "
    "AND e.id=NEW.recipient_employee_id AND e.company_id=NEW.company_id AND "
    "e.lifecycle_status='active' "
    "AND w.person_id=e.person_id AND w.runtime_type IN ('hermes', 'openclaw') AND "
    "w.deployment_mode='docker' "
    "AND p.status NOT IN ('completed', 'cancelled', 'rejected') AND ("
    "(NEW.purpose='management' AND p.management_employee_id=e.id "
    "AND (p.management_person_id IS NULL OR p.management_person_id=e.person_id)) OR "
    "(NEW.purpose='review' AND EXISTS (SELECT 1 FROM review_requests r JOIN tasks t ON "
    "t.id=r.task_id "
    "WHERE r.id=NEW.review_request_id AND r.reviewer_employee_id=e.id AND r.status='open' "
    "AND r.verdict IS NULL AND t.status='in_review' AND NOT EXISTS (SELECT 1 FROM "
    "review_requests newer "
    "WHERE newer.task_id=r.task_id AND newer.id>r.id))))))"
)
_INVALID_DUPLICATE = (
    "NEW.duplicate_of_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM management_dispatches d "
    "WHERE d.id=NEW.duplicate_of_id AND d.id<>NEW.id AND d.work_session_id IS NOT NULL "
    "AND d.purpose='review' AND NEW.purpose='review' AND d.company_id=NEW.company_id "
    "AND d.project_id=NEW.project_id AND d.task_id=NEW.task_id AND "
    "d.review_request_id=NEW.review_request_id)"
)
TRIGGERS = (
    (
        "management_dispatch_source_valid",
        "INSERT",
        _INVALID_SOURCE,
        "invalid management dispatch source",
    ),
    (
        "management_dispatch_identity_immutable",
        "UPDATE",
        _IDENTITY_CHANGED,
        "management dispatch identity is immutable",
    ),
    (
        "management_dispatch_link_once",
        "UPDATE",
        "OLD.work_session_id IS NOT NULL AND (NEW.work_session_id IS NOT "
        "OLD.work_session_id OR NEW.recipient_employee_id IS NOT OLD.recipient_employee_id)",
        "management dispatch link is immutable",
    ),
    (
        "management_dispatch_resolution_once",
        "UPDATE",
        "OLD.resolution IS NOT NULL AND (NEW.resolution IS NOT OLD.resolution OR "
        "NEW.resolution_reason IS NOT OLD.resolution_reason OR NEW.duplicate_of_id IS NOT "
        "OLD.duplicate_of_id)",
        "management dispatch resolution is immutable",
    ),
    ("management_dispatch_no_delete", "DELETE", "1", "management dispatch identity is immutable"),
    (
        "management_dispatch_recipient_insert",
        "INSERT",
        _INVALID_RECIPIENT,
        "invalid management dispatch recipient",
    ),
    (
        "management_dispatch_recipient_update",
        "UPDATE",
        "NEW.recipient_employee_id IS NOT OLD.recipient_employee_id AND ("
        + _INVALID_RECIPIENT
        + ")",
        "invalid management dispatch recipient",
    ),
    (
        "management_dispatch_link_insert",
        "INSERT",
        _INVALID_LINK,
        "invalid management dispatch link",
    ),
    (
        "management_dispatch_link_update",
        "UPDATE",
        "OLD.work_session_id IS NULL AND (" + _INVALID_LINK + ")",
        "invalid management dispatch link",
    ),
    (
        "management_dispatch_duplicate_insert",
        "INSERT",
        _INVALID_DUPLICATE,
        "invalid management dispatch duplicate",
    ),
    (
        "management_dispatch_duplicate_update",
        "UPDATE",
        "OLD.duplicate_of_id IS NULL AND (" + _INVALID_DUPLICATE + ")",
        "invalid management dispatch duplicate",
    ),
)


def upgrade():
    op.create_table(
        "management_dispatches",
        sa.Column("source_event_id", sa.Integer(), sa.ForeignKey("events.id"), nullable=False),
        sa.Column("source_type", sa.String(100), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("tasks.id"), nullable=True),
        sa.Column(
            "review_request_id", sa.Integer(), sa.ForeignKey("review_requests.id"), nullable=True
        ),
        sa.Column(
            "recipient_employee_id", sa.Integer(), sa.ForeignKey("employees.id"), nullable=True
        ),
        sa.Column(
            "work_session_id", sa.Integer(), sa.ForeignKey("work_sessions.id"), nullable=True
        ),
        sa.Column(
            "duplicate_of_id",
            sa.Integer(),
            sa.ForeignKey("management_dispatches.id"),
            nullable=True,
        ),
        sa.Column("checked_at", sa.DateTime(), nullable=True),
        sa.Column("waiting_reason", sa.String(64), nullable=True),
        sa.Column("details_json", sa.JSON(), nullable=False),
        sa.Column("resolution", sa.String(16), nullable=True),
        sa.Column("resolution_reason", sa.String(64), nullable=True),
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "(waiting_reason IS NULL OR length(waiting_reason) BETWEEN 1 AND 64) AND "
            "(resolution_reason IS NULL OR length(resolution_reason) BETWEEN 1 AND 64) AND "
            "length(CAST(details_json AS TEXT)) <= 1024",
            name="ck_management_dispatch_details_bound",
        ),
        sa.CheckConstraint(
            "(resolution = 'duplicate' AND duplicate_of_id IS NOT NULL) OR "
            "(duplicate_of_id IS NULL AND (resolution IS NULL OR resolution <> 'duplicate'))",
            name="ck_management_dispatch_duplicate",
        ),
        sa.CheckConstraint(
            "work_session_id IS NULL OR (resolution IS NULL AND recipient_employee_id IS "
            "NOT NULL AND waiting_reason IS NULL AND company_id IS NOT NULL AND project_id "
            "IS NOT NULL)",
            name="ck_management_dispatch_link",
        ),
        sa.CheckConstraint(
            "(purpose = 'management' AND source_type <> 'task.review_named' AND "
            "review_request_id IS NULL) OR (purpose = 'review' AND source_type = "
            "'task.review_named')",
            name="ck_management_dispatch_purpose",
        ),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('invalid', 'obsolete', 'duplicate')",
            name="ck_management_dispatch_resolution",
        ),
        sa.CheckConstraint(
            "(resolution IS NULL AND resolution_reason IS NULL) OR (resolution IS NOT NULL "
            "AND resolution_reason IS NOT NULL)",
            name="ck_management_dispatch_resolution_reason",
        ),
        sa.CheckConstraint(
            "source_type IN ('project.replan_required', 'task.assignment_required', "
            "'task.blocked', 'task.failed', 'task.review_failed', 'task.review_named', "
            "'task.review_required', 'task.runtime_unavailable')",
            name="ck_management_dispatch_source_type",
        ),
    )
    op.create_index(
        "ix_management_dispatch_pending",
        "management_dispatches",
        ["resolution", "work_session_id", "checked_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_management_dispatch_scope",
        "management_dispatches",
        ["company_id", "project_id", "id"],
        unique=False,
    )
    op.create_index(
        "uq_management_dispatch_review",
        "management_dispatches",
        ["review_request_id"],
        unique=True,
        sqlite_where=sa.text("work_session_id IS NOT NULL"),
    )
    op.create_index(
        "uq_management_dispatch_session", "management_dispatches", ["work_session_id"], unique=True
    )
    op.create_index(
        "uq_management_dispatch_source", "management_dispatches", ["source_event_id"], unique=True
    )
    for name, timing, condition, message in TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {timing} ON management_dispatches WHEN {condition} "
            f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
        )


def downgrade():
    # Refuse before any DDL: dropping even unresolved observations permits replay.
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM management_dispatches")):
        raise RuntimeError("cannot discard management dispatch dedup history")
    op.drop_table("management_dispatches")

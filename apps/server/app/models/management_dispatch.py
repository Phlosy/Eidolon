"""Durable source observations and once-only links; WorkSession owns execution facts."""

from datetime import datetime

from sqlalchemy import (
    DDL,
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    event,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# Revision-local vocabulary. The consumer tests compare this with the domain contract.
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


class ManagementDispatch(TimestampMixin, Base):
    __tablename__ = "management_dispatches"
    __table_args__ = (
        Index("uq_management_dispatch_source", "source_event_id", unique=True),
        Index("uq_management_dispatch_session", "work_session_id", unique=True),
        Index(
            "uq_management_dispatch_review",
            "review_request_id",
            unique=True,
            sqlite_where=text("work_session_id IS NOT NULL"),
            postgresql_where=text("work_session_id IS NOT NULL"),
        ),
        Index(
            "ix_management_dispatch_pending", "resolution", "work_session_id", "checked_at", "id"
        ),
        Index("ix_management_dispatch_scope", "company_id", "project_id", "id"),
        CheckConstraint(
            "source_type IN (" + ", ".join(repr(value) for value in SOURCE_TYPES) + ")",
            name="ck_management_dispatch_source_type",
        ),
        CheckConstraint(
            "(purpose = 'management' AND source_type <> 'task.review_named' AND "
            "review_request_id IS NULL) OR "
            "(purpose = 'review' AND source_type = 'task.review_named')",
            name="ck_management_dispatch_purpose",
        ),
        CheckConstraint(
            "resolution IS NULL OR resolution IN ('invalid', 'obsolete', 'duplicate')",
            name="ck_management_dispatch_resolution",
        ),
        CheckConstraint(
            "(resolution IS NULL AND resolution_reason IS NULL) OR "
            "(resolution IS NOT NULL AND resolution_reason IS NOT NULL)",
            name="ck_management_dispatch_resolution_reason",
        ),
        CheckConstraint(
            "(resolution = 'duplicate' AND duplicate_of_id IS NOT NULL) OR "
            "(duplicate_of_id IS NULL AND (resolution IS NULL OR resolution <> 'duplicate'))",
            name="ck_management_dispatch_duplicate",
        ),
        CheckConstraint(
            "work_session_id IS NULL OR (resolution IS NULL AND recipient_employee_id IS NOT NULL "
            "AND waiting_reason IS NULL AND company_id IS NOT NULL AND project_id IS NOT NULL)",
            name="ck_management_dispatch_link",
        ),
        CheckConstraint(
            "(waiting_reason IS NULL OR length(waiting_reason) BETWEEN 1 AND 64) AND "
            "(resolution_reason IS NULL OR length(resolution_reason) BETWEEN 1 AND 64) AND "
            "length(CAST(details_json AS TEXT)) <= 1024",
            name="ck_management_dispatch_details_bound",
        ),
    )

    source_event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    source_type: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(20))
    # Invalid source associations stay null instead of copying a foreign target.
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    review_request_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_requests.id"), nullable=True
    )
    recipient_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    work_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_sessions.id"), nullable=True
    )
    duplicate_of_id: Mapped[int | None] = mapped_column(
        ForeignKey("management_dispatches.id"), nullable=True
    )
    checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    waiting_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


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
for _name, _timing, _condition, _message in TRIGGERS:
    event.listen(
        ManagementDispatch.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER {_name} BEFORE {_timing} ON management_dispatches WHEN {_condition} "
            f"BEGIN SELECT RAISE(ABORT, '{_message}'); END"
        ).execute_if(dialect="sqlite"),
    )


@event.listens_for(ManagementDispatch, "before_update")
def _immutable_observation(_mapper, _connection, target):
    state = inspect(target)
    for name in _IDENTITY_FIELDS:
        if state.attrs[name].history.has_changes():
            raise ValueError("management dispatch identity is immutable")
    table = ManagementDispatch.__table__
    previous = (
        _connection.execute(
            select(
                table.c.work_session_id,
                table.c.recipient_employee_id,
                table.c.resolution,
                table.c.resolution_reason,
                table.c.duplicate_of_id,
            ).where(table.c.id == state.identity[0])
        )
        .mappings()
        .one()
    )
    if previous["work_session_id"] is not None and any(
        getattr(target, name) != previous[name]
        for name in ("work_session_id", "recipient_employee_id")
    ):
        raise ValueError("management dispatch link is immutable")
    if previous["resolution"] is not None and any(
        getattr(target, name) != previous[name]
        for name in ("resolution", "resolution_reason", "duplicate_of_id")
    ):
        raise ValueError("management dispatch resolution is immutable")


@event.listens_for(ManagementDispatch, "before_delete")
def _retain_observation(_mapper, _connection, _target):
    raise ValueError("management dispatch identity is immutable")

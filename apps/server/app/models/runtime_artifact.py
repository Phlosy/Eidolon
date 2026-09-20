"""One immutable acceptance receipt per original run; bodies and ownership stay in Drive."""

from datetime import datetime

from sqlalchemy import DDL, JSON, CheckConstraint, DateTime, ForeignKey, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow
from app.models.drive import DriveNode
from app.models.project import WorkSession
from app.models.runtime_execution import RuntimeAttempt


class RuntimeArtifactCollection(Base):
    __tablename__ = "runtime_artifact_collections"
    __table_args__ = (
        CheckConstraint("length(digest) = 64", name="ck_artifact_collection_digest"),
        CheckConstraint(
            "json_valid(entries_json) AND json_type(entries_json) = 'array' "
            "AND json_array_length(entries_json) <= 32",
            name="ck_artifact_collection_entries",
        ),
    )

    attempt_id: Mapped[int] = mapped_column(
        ForeignKey("runtime_attempts.id"), primary_key=True, autoincrement=False
    )
    digest: Mapped[str] = mapped_column(String(64))
    entries_json: Mapped[list] = mapped_column(JSON)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


_ORIGIN = (
    "EXISTS (SELECT 1 FROM runtime_attempts a JOIN work_sessions w ON w.id=a.work_session_id "
    "JOIN tasks t ON t.id=w.task_id JOIN projects p ON p.id=t.project_id "
    "WHERE a.id=NEW.attempt_id AND a.kind='worker' AND a.status='completed' "
    "AND a.runtime_instance_id IS NOT NULL AND a.remote_run_id IS NOT NULL "
    "AND (a.host_outcome IS NULL OR a.host_outcome='completed') "
    "AND json_type(a.artifact_context_json, '$.required_types')='array' "
    "AND w.runtime_type <> 'mock' AND w.status='running' AND t.status='in_progress' "
    "AND w.person_id IS NOT NULL AND w.company_id=p.company_id)"
)
_ENTRY_INVALID = (
    "EXISTS (SELECT 1 FROM json_each(NEW.entries_json) e WHERE e.type <> 'object' "
    "OR (SELECT COUNT(*) FROM json_each(e.value)) <> 2 "
    "OR json_type(e.value, '$.source_ref') IS NOT 'text' "
    "OR length(json_extract(e.value, '$.source_ref')) NOT BETWEEN 1 AND 1024 "
    "OR json_type(e.value, '$.revision_id') IS NOT 'integer' "
    "OR NOT EXISTS (SELECT 1 FROM drive_revisions r JOIN drive_nodes n ON n.id=r.node_id "
    "JOIN runtime_attempts a ON a.id=NEW.attempt_id JOIN work_sessions w ON w.id=a.work_session_id "
    "JOIN tasks t ON t.id=w.task_id WHERE r.id=json_extract(e.value, '$.revision_id') "
    "AND r.content_path IS NOT NULL AND r.author_person_id=w.person_id "
    "AND n.kind='document' AND n.work_session_id=w.id AND n.task_id=w.task_id "
    "AND n.company_id=w.company_id AND n.project_id=t.project_id "
    "AND n.owner_person_id=w.person_id AND n.owner_employee_id=w.employee_id))"
)
_DUPLICATE = (
    "EXISTS (SELECT 1 FROM json_each(NEW.entries_json) e "
    "GROUP BY json_extract(e.value, '$.source_ref') HAVING COUNT(*) > 1) OR "
    "EXISTS (SELECT 1 FROM json_each(NEW.entries_json) e "
    "GROUP BY json_extract(e.value, '$.revision_id') HAVING COUNT(*) > 1)"
)
_MISSING_TYPES = (
    "EXISTS (SELECT 1 FROM runtime_attempts a, "
    "json_each(a.artifact_context_json, '$.required_types') required "
    "WHERE a.id=NEW.attempt_id AND NOT EXISTS (SELECT 1 FROM json_each(NEW.entries_json) e "
    "JOIN drive_revisions r ON r.id=json_extract(e.value, '$.revision_id') "
    "JOIN drive_nodes n ON n.id=r.node_id WHERE n.doc_type=required.value))"
)
_NODE_REFERENCED = (
    "EXISTS (SELECT 1 FROM runtime_artifact_collections c, json_each(c.entries_json) e "
    "JOIN drive_revisions r ON r.id=json_extract(e.value, '$.revision_id') WHERE r.node_id=OLD.id)"
)
_NODE_IDENTITY = (
    "id",
    "kind",
    "company_id",
    "project_id",
    "work_session_id",
    "task_id",
    "owner_employee_id",
    "owner_person_id",
    "doc_type",
)
_WORK_REFERENCED = (
    "EXISTS (SELECT 1 FROM runtime_artifact_collections c JOIN runtime_attempts a "
    "ON a.id=c.attempt_id WHERE a.work_session_id=OLD.id)"
)
COLLECTION_GUARDS = (
    (
        "runtime_artifact_collections",
        "artifact_collection_stop_fence",
        "INSERT",
        "EXISTS (SELECT 1 FROM runtime_attempts a WHERE a.id=NEW.attempt_id "
        "AND a.stop_requested_at IS NOT NULL)",
    ),
    (
        "runtime_artifact_collections",
        "artifact_collection_valid",
        "INSERT",
        f"NOT ({_ORIGIN}) OR {_ENTRY_INVALID} OR {_DUPLICATE} OR {_MISSING_TYPES} OR "
        "NEW.digest GLOB '*[^0-9a-f]*'",
    ),
    (
        "runtime_artifact_collections",
        "artifact_collection_one_per_work",
        "INSERT",
        "EXISTS (SELECT 1 FROM runtime_artifact_collections c "
        "JOIN runtime_attempts old ON old.id=c.attempt_id "
        "JOIN runtime_attempts current ON current.id=NEW.attempt_id "
        "WHERE old.work_session_id=current.work_session_id)",
    ),
    ("runtime_artifact_collections", "artifact_collection_immutable", "UPDATE", "1"),
    ("runtime_artifact_collections", "artifact_collection_no_delete", "DELETE", "1"),
    (
        "drive_nodes",
        "artifact_collection_node_immutable",
        "UPDATE",
        _NODE_REFERENCED
        + " AND ("
        + " OR ".join(f"NEW.{c} IS NOT OLD.{c}" for c in _NODE_IDENTITY)
        + ")",
    ),
    ("drive_nodes", "artifact_collection_node_no_delete", "DELETE", _NODE_REFERENCED),
    (
        "drive_nodes",
        "artifact_collection_node_no_replace",
        "INSERT",
        _NODE_REFERENCED.replace("OLD.id", "NEW.id"),
    ),
    (
        "runtime_attempts",
        "artifact_collection_attempt_no_replace",
        "INSERT",
        "EXISTS (SELECT 1 FROM runtime_artifact_collections c WHERE c.attempt_id=NEW.id)",
    ),
    (
        "work_sessions",
        "artifact_collection_work_no_replace",
        "INSERT",
        _WORK_REFERENCED.replace("OLD.id", "NEW.id"),
    ),
    ("work_sessions", "artifact_collection_work_no_delete", "DELETE", _WORK_REFERENCED),
)
_TABLES = {
    t.name: t
    for t in (
        RuntimeArtifactCollection.__table__,
        DriveNode.__table__,
        RuntimeAttempt.__table__,
        WorkSession.__table__,
    )
}
for _table, _name, _operation, _condition in COLLECTION_GUARDS:
    event.listen(
        _TABLES[_table],
        "after_create",
        DDL(
            f"CREATE TRIGGER {_name} BEFORE {_operation} ON {_table} WHEN {_condition} "
            "BEGIN SELECT RAISE(ABORT, 'invalid or immutable artifact collection'); END"
        ).execute_if(dialect="sqlite"),
    )

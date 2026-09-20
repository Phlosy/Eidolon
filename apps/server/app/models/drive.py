"""Drive domain (v0.3): DriveNode / DriveRevision / DriveCollaborator.

See docs/design-v0.3-workspace.md §2. Files are real on disk under
``{data_root}/drive/``; these tables only index them (path, ownership,
versioning, hashes). The deprecated ``artifacts`` table is migrated into
project-zone DriveNodes at startup (see services/drive_migration.py).
"""

from datetime import datetime

from sqlalchemy import DDL, DateTime, ForeignKey, Integer, String, Text, event
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, utcnow
from app.models.enums import DriveNodeKind, DriveZone
from app.models.project import Project


class DriveNode(TimestampMixin, Base):
    __tablename__ = "drive_nodes"

    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id"), nullable=True, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("drive_nodes.id"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), default=DriveNodeKind.document.value)
    name: Mapped[str] = mapped_column(String(300))
    # Disk path relative to settings.data_root (e.g. "drive/projects/{slug}/docs/prd.md").
    path: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    zone: Mapped[str] = mapped_column(String(50), default=DriveZone.projects.value, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id"), nullable=True, index=True
    )
    doc_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # deprecated（R1.4）：属主读口径已切到 owner_person_id；列保留作兼容镜像，随表留存不删。
    owner_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True, index=True
    )
    owner_person_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    current_version: Mapped[int] = mapped_column(default=1)
    # Link to the work session that produced this document (v0.3, nullable).
    work_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("work_sessions.id"), nullable=True
    )
    # M2.6（H2）：**产出归属** —— 这个交付物是哪个 Task 的产物。
    # 产出时写入、此后不可变；人上传/历史文档为 NULL（不允许猜）。
    # 与 `work_session_id` 合起来 = "谁在哪次会话产出的"；使用关系在 `artifact_links`。
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), nullable=True, index=True)

    project: Mapped[Project | None] = relationship()


class DriveRevision(Base):
    __tablename__ = "drive_revisions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("drive_nodes.id"), index=True)
    version: Mapped[int] = mapped_column()
    sha256: Mapped[str] = mapped_column(String(64))
    # deprecated（R1.4）：署名读口径已切到 author_person_id；列保留作兼容镜像，随表留存不删。
    author_employee_id: Mapped[int | None] = mapped_column(
        ForeignKey("employees.id"), nullable=True
    )
    author_person_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Explicitly preserved canonical bytes; historical revisions stay unknown.
    content_path: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DriveCollaborator(TimestampMixin, Base):
    __tablename__ = "drive_collaborators"

    node_id: Mapped[int] = mapped_column(ForeignKey("drive_nodes.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")


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
for _name, _operation, _condition in CONTENT_GUARDS:
    event.listen(
        DriveRevision.__table__,
        "after_create",
        DDL(
            f"CREATE TRIGGER {_name} BEFORE {_operation} ON drive_revisions WHEN {_condition} "
            "BEGIN SELECT RAISE(ABORT, 'invalid or immutable revision content'); END"
        ).execute_if(dialect="sqlite"),
    )

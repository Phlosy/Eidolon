"""Drive domain (v0.3): DriveNode / DriveRevision / DriveCollaborator.

See docs/design-v0.3-workspace.md §2. Files are real on disk under
``{data_root}/drive/``; these tables only index them (path, ownership,
versioning, hashes). The deprecated ``artifacts`` table is migrated into
project-zone DriveNodes at startup (see services/drive_migration.py).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
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


class DriveCollaborator(TimestampMixin, Base):
    __tablename__ = "drive_collaborators"

    node_id: Mapped[int] = mapped_column(ForeignKey("drive_nodes.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="viewer")

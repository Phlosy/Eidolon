"""Artifact compat layer (v0.3).

The ``artifacts`` table is deprecated and no longer written. Artifacts are now
DriveNodes (kind=document, zone=projects); this module maps them back to the
legacy ArtifactOut shape so old clients keep working
(docs/design-v0.3-workspace.md §2 "Artifact 与 Drive 的关系").
"""

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.drive import DriveNode
from app.models.enums import DriveNodeKind, DriveZone
from app.models.project import Project
from app.repositories import drive as drive_repo
from app.schemas.project import ArtifactOut
from app.services import drive as drive_service

# Re-export for callers that still reason in artifact types.
TYPE_TO_DIR = drive_service.TYPE_TO_DIR


def record_project_artifact(
    db: Session,
    project: Project,
    *,
    artifact_type: str,
    title: str,
    content: str,
    author_id: int | None = None,
    work_session_id: int | None = None,
) -> DriveNode:
    """Write a produced artifact into the project's drive folder (revision v1)."""
    return drive_service.create_project_document(
        db,
        project,
        doc_type=artifact_type,
        title=title,
        content=content,
        owner_employee_id=author_id,
        work_session_id=work_session_id,
    )


def list_artifact_nodes(
    db: Session, project_id: int | None = None, artifact_type: str | None = None
) -> list[DriveNode]:
    stmt = (
        select(DriveNode)
        .where(
            DriveNode.kind == DriveNodeKind.document.value,
            DriveNode.zone == DriveZone.projects.value,
            DriveNode.project_id.is_not(None),
        )
        .order_by(desc(DriveNode.id))
    )
    if project_id is not None:
        stmt = stmt.where(DriveNode.project_id == project_id)
    if artifact_type is not None:
        stmt = stmt.where(DriveNode.doc_type == artifact_type)
    return list(db.scalars(stmt))


def artifact_out(db: Session, node: DriveNode) -> ArtifactOut:
    """Map a project-zone document node to the legacy artifact shape."""
    project = db.get(Project, node.project_id) if node.project_id else None
    revision = drive_repo.get_revision(db, node.id, node.current_version)
    return ArtifactOut(
        id=node.id,
        company_id=project.company_id if project else 0,
        project_id=node.project_id or 0,
        task_id=None,  # deprecated going forward; kept in the response shape
        type=node.doc_type or "other",
        title=node.name,
        content=drive_service.read_content(node) or "",
        path=str(drive_service.abs_path(node)),
        sha256=revision.sha256 if revision else None,
        work_session_id=node.work_session_id,
        version=node.current_version,
        status="draft",
        author_id=node.owner_employee_id,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )

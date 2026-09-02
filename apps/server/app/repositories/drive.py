"""Drive repositories: pure data access for drive_nodes / revisions / collaborators."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.drive import DriveCollaborator, DriveNode, DriveRevision
from app.models.enums import DriveNodeKind


def list_nodes(
    db: Session,
    zone: str | None = None,
    project_id: int | None = None,
    kind: str | None = None,
    doc_type: str | None = None,
) -> list[DriveNode]:
    stmt = select(DriveNode).order_by(DriveNode.id)
    if zone is not None:
        stmt = stmt.where(DriveNode.zone == zone)
    if project_id is not None:
        stmt = stmt.where(DriveNode.project_id == project_id)
    if kind is not None:
        stmt = stmt.where(DriveNode.kind == kind)
    if doc_type is not None:
        stmt = stmt.where(DriveNode.doc_type == doc_type)
    return list(db.scalars(stmt))


def get_node(db: Session, node_id: int) -> DriveNode | None:
    return db.get(DriveNode, node_id)


def get_node_by_path(db: Session, path: str) -> DriveNode | None:
    return db.scalars(select(DriveNode).where(DriveNode.path == path)).first()


def create_node(db: Session, **fields) -> DriveNode:
    node = DriveNode(**fields)
    db.add(node)
    db.flush()
    return node


def create_revision(db: Session, **fields) -> DriveRevision:
    revision = DriveRevision(**fields)
    db.add(revision)
    db.flush()
    return revision


def list_revisions(db: Session, node_id: int) -> list[DriveRevision]:
    return list(
        db.scalars(
            select(DriveRevision)
            .where(DriveRevision.node_id == node_id)
            .order_by(DriveRevision.version.desc())
        )
    )


def get_revision(db: Session, node_id: int, version: int) -> DriveRevision | None:
    return db.scalars(
        select(DriveRevision).where(
            DriveRevision.node_id == node_id, DriveRevision.version == version
        )
    ).first()


def list_collaborators(db: Session, node_id: int) -> list[DriveCollaborator]:
    return list(db.scalars(select(DriveCollaborator).where(DriveCollaborator.node_id == node_id)))


def list_collaborators_by_employee(db: Session, employee_id: int) -> list[DriveCollaborator]:
    return list(
        db.scalars(select(DriveCollaborator).where(DriveCollaborator.employee_id == employee_id))
    )


def get_collaborator(db: Session, node_id: int, employee_id: int) -> DriveCollaborator | None:
    return db.scalars(
        select(DriveCollaborator).where(
            DriveCollaborator.node_id == node_id, DriveCollaborator.employee_id == employee_id
        )
    ).first()


def create_collaborator(db: Session, **fields) -> DriveCollaborator:
    collaborator = DriveCollaborator(**fields)
    db.add(collaborator)
    db.flush()
    return collaborator


def delete_collaborator(db: Session, collaborator: DriveCollaborator) -> None:
    db.delete(collaborator)
    db.flush()


def count_documents_by_owner(db: Session, owner_employee_id: int) -> int:
    return int(
        db.scalar(
            select(func.count(DriveNode.id)).where(
                DriveNode.owner_employee_id == owner_employee_id,
                DriveNode.kind == DriveNodeKind.document.value,
            )
        )
        or 0
    )

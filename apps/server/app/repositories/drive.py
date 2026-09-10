"""Drive repositories: pure data access for drive_nodes / revisions / collaborators."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.models.drive import DriveCollaborator, DriveNode, DriveRevision
from app.models.enums import DriveNodeKind
from app.models.organization import Employee
from app.models.project import Project
from app.repositories import persons as person_repo


def list_nodes(
    db: Session,
    zone: str | None = None,
    project_id: int | None = None,
    kind: str | None = None,
    doc_type: str | None = None,
) -> list[DriveNode]:
    stmt = select(DriveNode).order_by(DriveNode.id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(DriveNode.company_id == identity.company_id)
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
    stmt = select(DriveNode).where(DriveNode.id == node_id)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(DriveNode.company_id == identity.company_id)
    return db.scalar(stmt)


def get_node_by_path(db: Session, path: str) -> DriveNode | None:
    stmt = select(DriveNode).where(DriveNode.path == path)
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(DriveNode.company_id == identity.company_id)
    return db.scalars(stmt).first()


def create_node(db: Session, **fields) -> DriveNode:
    if "company_id" not in fields:
        identity = get_request_identity()
        if identity is not None:
            fields["company_id"] = identity.company_id
        elif fields.get("project_id") is not None:
            project = db.get(Project, fields["project_id"])
            fields["company_id"] = project.company_id if project else None
        elif fields.get("owner_employee_id") is not None:
            employee = db.get(Employee, fields["owner_employee_id"])
            fields["company_id"] = employee.company_id if employee else None
        elif fields.get("parent_id") is not None:
            parent = db.get(DriveNode, fields["parent_id"])
            if parent is not None and parent.company_id is not None:
                fields["company_id"] = parent.company_id
    # 双写（R1.4）：owner_employee_id（deprecated 镜像）+ owner_person_id（权威口径）；
    # owner 为 NULL 的节点（zone 根/公共资源）没有人称可解析，跳过。
    if fields.get("owner_employee_id") is not None:
        fields.setdefault(
            "owner_person_id", person_repo.write_person_id(db, fields["owner_employee_id"])
        )
    # 带 path 的创建一律碰撞安全（所有 drive 目录的唯一切入点）。
    # SQLite：INSERT ... ON CONFLICT DO NOTHING + 回查 —— 无异常、不毒化
    # 会话、任意事务内可用；并发创建同一目录（入职/补收敛/教程轮询）由唯一
    # 索引兜底，谁先提交谁赢，后到者返回赢家行（幂等语义）。
    if fields.get("path"):
        return _create_node_collision_safe(db, fields)
    node = DriveNode(**fields)
    db.add(node)
    db.flush()
    return node


def _create_node_collision_safe(db: Session, fields: dict) -> DriveNode:
    from app.models.base import utcnow
    from app.models.enums import DriveZone

    path: str = fields["path"]
    values = {
        "company_id": fields.get("company_id"),
        "parent_id": fields.get("parent_id"),
        "kind": fields.get("kind", DriveNodeKind.document.value),
        "name": fields["name"],
        "path": path,
        "zone": fields.get("zone", DriveZone.projects.value),
        "project_id": fields.get("project_id"),
        "doc_type": fields.get("doc_type"),
        "owner_employee_id": fields.get("owner_employee_id"),
        "owner_person_id": fields.get("owner_person_id"),
        "current_version": fields.get("current_version", 1),
        "work_session_id": fields.get("work_session_id"),
        "created_at": fields.get("created_at") or utcnow(),
        "updated_at": fields.get("updated_at") or utcnow(),
    }
    if db.get_bind().dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = (
            sqlite_insert(DriveNode)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["path"])
        )
        db.execute(stmt)
    else:
        # 非 SQLite（如 postgres）：唯一索引 + 冲突即回查（回查前需要 flush 让本
        # 事务内的插入先落位，冲突则由索引拦住）
        db.execute(
            DriveNode.__table__.insert().values(**values).prefix_with("ON CONFLICT DO NOTHING")
        )
    return db.scalar(select(DriveNode).where(DriveNode.path == path))


def create_revision(db: Session, **fields) -> DriveRevision:
    # 双写（R1.4）：author_employee_id（deprecated 镜像）+ author_person_id（权威口径）
    if fields.get("author_employee_id") is not None:
        fields.setdefault(
            "author_person_id", person_repo.write_person_id(db, fields["author_employee_id"])
        )
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
                # R1.4：属主口径切 owner_person_id（单一入口换算，带旧口径回落）
                person_repo.read_criterion(
                    db, owner_employee_id, DriveNode.owner_person_id, DriveNode.owner_employee_id
                ),
                DriveNode.kind == DriveNodeKind.document.value,
            )
        )
        or 0
    )

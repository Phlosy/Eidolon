"""Drive service (v0.3): folders, documents, revisions, zone permissions.

Files are real on disk under ``{data_root}/drive/``; the DB only indexes them.
Zone write rules (MVP, enforced here — docs/design-v0.3-workspace.md §2):
projects zone → project members write, everyone reads; knowledge/skills/
handbook → author writes, company members read. Endpoints may pass an
``employee_id`` actor for employee-level author and project checks.
"""

import hashlib
import re
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.request_context import get_request_identity
from app.events.bus import bus
from app.models.drive import DriveNode
from app.models.enums import ArtifactType, DriveNodeKind, DriveZone
from app.models.organization import Company
from app.models.project import Project
from app.repositories import drive as drive_repo
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.repositories import project_delivery as delivery_repo

ZONES = [z.value for z in DriveZone]
PROJECT_SUBDIRS = ("docs", "source", "tests", "release")
TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
GENERATED_BINARY_TYPES = {
    ".docx": "docx",
    ".pptx": "pptx",
    ".pdf": "pdf",
    ".zip": "release",
}
UPLOAD_TYPES = {
    ".md": "note",
    ".markdown": "note",
    ".docx": "docx",
    ".pdf": "pdf",
}

# artifact/doc type -> project subfolder (v0.3 mapping)
TYPE_TO_DIR = {
    ArtifactType.prd.value: "docs",
    ArtifactType.research_report.value: "docs",
    ArtifactType.architecture.value: "docs",
    ArtifactType.plan.value: "docs",
    ArtifactType.other.value: "docs",
    ArtifactType.source_code.value: "source",
    ArtifactType.test_report.value: "tests",
    ArtifactType.release.value: "release",
    ArtifactType.readme.value: "release",
}


def slugify(text: str, fallback: str = "node") -> str:
    slug = re.sub(r"[^0-9A-Za-z一-鿿]+", "-", text.lower()).strip("-")
    return slug[:60] or fallback


def drive_root() -> Path:
    return Path(settings.data_root) / "drive"


def abs_path(node: DriveNode) -> Path:
    return Path(settings.data_root) / node.path


def read_content(node: DriveNode) -> str | None:
    path = abs_path(node)
    if node.kind != DriveNodeKind.document.value or not path.exists():
        return None
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        return None
    return path.read_text(encoding="utf-8")


def _unique_path(db: Session, desired: str) -> str:
    """drive_nodes.path is unique; suffix -2/-3/... on collision."""
    if drive_repo.get_node_by_path(db, desired) is None:
        return desired
    stem, dot, suffix = desired.rpartition(".")
    base, ext = (stem, f".{suffix}") if dot else (desired, "")
    counter = 2
    while drive_repo.get_node_by_path(db, f"{base}-{counter}{ext}") is not None:
        counter += 1
    return f"{base}-{counter}{ext}"


def _create_node(db: Session, *, path: str, on_disk: Path, **fields) -> DriveNode:
    node = drive_repo.create_node(db, path=path, **fields)
    if node.kind == DriveNodeKind.folder.value:
        on_disk.mkdir(parents=True, exist_ok=True)
    return node  # 这里不 commit，所以也不发事件：事件只在真正提交之后发


def publish_drive(kind: str, node: DriveNode) -> None:
    """Drive 写操作要发事件（以前 Drive 是唯一"静默"的域）。

    教程的 COMPANY_DOCUMENT_CREATED 靠 GET /tutorial 里的 reconcile 才推进；
    Drive 不发事件，前端就不知道该重取，实测外部创建文档后 15s 内教程毫无反应。
    事件只是"该重算了"的信号而不是状态本身，所以偶发的多余事件只会多一次重取。
    """
    bus.publish(
        f"drive.{kind}",
        {"id": node.id, "kind": node.kind, "zone": node.zone, "path": node.path, "name": node.name},
        company_id=node.company_id,
        project_id=node.project_id,
        actor_employee_id=node.owner_employee_id,
    )


# ---- seed / project folder structure ----


def company_drive_path(db: Session, path: str, company_id: int) -> str:
    """Keep the original installation paths stable and namespace later companies."""
    first_company_id = db.scalar(select(Company.id).order_by(Company.id).limit(1))
    if company_id == first_company_id:
        return path
    suffix = path.removeprefix("drive/")
    return f"drive/companies/{company_id}/{suffix}"


def _zone_root_path(db: Session, zone: str, company_id: int | None = None) -> str:
    identity = get_request_identity()
    company_id = company_id or (identity.company_id if identity else None)
    return company_drive_path(db, f"drive/{zone}", company_id) if company_id else f"drive/{zone}"


def zone_root_path(db: Session, zone: str, company_id: int | None = None) -> str:
    """公开版 zone 根路径（provisioner 等跨模块使用，避免摸私有函数）。"""
    return _zone_root_path(db, zone, company_id)


def ensure_zone_roots(db: Session, company_id: int | None = None) -> None:
    """Idempotently create the four zone root folders (drive/{zone}).

    并发安全由 drive_repo.create_node 统一兜底（savepoint + 回查已提交行 +
    短退避重试）—— 两个请求同时开同一目录不会撞 drive_nodes.path 唯一约束。
    """
    identity = get_request_identity()
    cid = company_id or (identity.company_id if identity else None)
    for zone in ZONES:
        path = _zone_root_path(db, zone, company_id)
        node = drive_repo.get_node_by_path(db, path)
        if node is None:
            node = _create_node(
                db,
                path=path,
                on_disk=Path(settings.data_root) / path,
                parent_id=None,
                kind=DriveNodeKind.folder.value,
                name=zone,
                zone=zone,
                company_id=cid,
            )
        # 启动期种子在没有请求上下文时建根目录，company_id 落为 NULL，而首个
        # 公司的 zone 路径与它相同；之后请求按 company 过滤就看不到这条根，
        # 「新建文档」会 404 zone root not found。根目录路径相同即同一家公司
        # 的地盘，直接把无主行收养到当前公司名下。
        if node is not None and node.company_id is None and cid is not None:
            node.company_id = cid
    db.flush()


def project_folder_name(db: Session, project: Project) -> str:
    """Deterministic, unique slug for a project's drive folder."""
    base = slugify(project.name, fallback=f"project-{project.id}")
    existing = drive_repo.get_node_by_path(
        db, f"{_zone_root_path(db, DriveZone.projects.value, project.company_id)}/{base}"
    )
    if existing is not None and existing.project_id != project.id:
        return f"{base}-{project.id}"
    return base


def ensure_project_folders(db: Session, project: Project) -> dict[str, DriveNode]:
    """Create (idempotently) the project folder + docs/source/tests/release."""
    ensure_zone_roots(db, project.company_id)
    zone_path = _zone_root_path(db, DriveZone.projects.value, project.company_id)
    root = drive_repo.get_node_by_path(db, f"{zone_path}/{project_folder_name(db, project)}")
    if root is None:
        zone_root = drive_repo.get_node_by_path(db, zone_path)
        root = _create_node(
            db,
            path=_unique_path(db, f"{zone_path}/{project_folder_name(db, project)}"),
            on_disk=abs_path(zone_root) / project_folder_name(db, project),
            parent_id=zone_root.id if zone_root else None,
            kind=DriveNodeKind.folder.value,
            name=project.name,
            zone=DriveZone.projects.value,
            project_id=project.id,
            owner_employee_id=project.owner_id,
        )
    folders = {"": root}
    for sub in PROJECT_SUBDIRS:
        path = f"{root.path}/{sub}"
        node = drive_repo.get_node_by_path(db, path)
        if node is None:
            node = _create_node(
                db,
                path=path,
                on_disk=abs_path(root) / sub,
                parent_id=root.id,
                kind=DriveNodeKind.folder.value,
                name=sub,
                zone=DriveZone.projects.value,
                project_id=project.id,
                owner_employee_id=project.owner_id,
            )
        folders[sub] = node
    db.flush()
    return folders


# ---- documents ----


def create_document(
    db: Session,
    *,
    parent: DriveNode,
    name: str,
    content: str,
    doc_type: str | None = None,
    project_id: int | None = None,
    owner_employee_id: int | None = None,
    work_session_id: int | None = None,
    message: str | None = None,
    commit: bool = False,
) -> DriveNode:
    """Create a document node: file on disk + row + DriveRevision v1."""
    filename = f"{slugify(name, fallback=doc_type or 'doc')}.md"
    path = _unique_path(db, f"{parent.path}/{filename}")
    node = drive_repo.create_node(
        db,
        parent_id=parent.id,
        kind=DriveNodeKind.document.value,
        name=name,
        path=path,
        zone=parent.zone,
        project_id=project_id if project_id is not None else parent.project_id,
        doc_type=doc_type,
        owner_employee_id=owner_employee_id,
        current_version=1,
        work_session_id=work_session_id,
    )
    target = abs_path(node)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    drive_repo.create_revision(
        db,
        node_id=node.id,
        version=1,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        author_employee_id=owner_employee_id,
        message=message,
    )
    if commit:
        db.commit()
        db.refresh(node)
        # 事件必须在 commit 之后发：bus.publish 用自己的 session，
        # 插在未提交的事务里会撞 SQLite 写锁（实测 database is locked）。
        publish_drive("created", node)
    else:
        db.flush()
    return node


def create_binary_document(
    db: Session,
    *,
    parent: DriveNode,
    name: str,
    content: bytes,
    extension: str,
    doc_type: str,
    project_id: int | None = None,
    owner_employee_id: int | None = None,
    message: str | None = None,
) -> DriveNode:
    """Create a generated binary file and exact Drive revision without committing.

    The caller owns the surrounding project/review transaction.
    """
    extension = extension.lower()
    if not extension.startswith("."):
        extension = f".{extension}"
    if extension not in GENERATED_BINARY_TYPES:
        raise ValueError(f"unsupported generated document extension: {extension}")
    path = _unique_path(
        db, f"{parent.path}/{slugify(Path(name).stem, fallback='document')}{extension}"
    )
    node = drive_repo.create_node(
        db,
        parent_id=parent.id,
        kind=DriveNodeKind.document.value,
        name=name,
        path=path,
        zone=parent.zone,
        project_id=project_id if project_id is not None else parent.project_id,
        doc_type=doc_type,
        owner_employee_id=owner_employee_id,
        current_version=1,
    )
    target = abs_path(node)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    drive_repo.create_revision(
        db,
        node_id=node.id,
        version=1,
        sha256=hashlib.sha256(content).hexdigest(),
        author_employee_id=owner_employee_id,
        message=message,
    )
    db.flush()  # 不 commit：事件由真正提交的上层负责
    return node


def create_markdown_document(
    db: Session,
    *,
    zone: str,
    name: str,
    content: str = "",
    parent_id: int | None = None,
    project_id: int | None = None,
    actor_employee_id: int | None = None,
) -> DriveNode:
    """原生「新建文档」（Markdown）：磁盘文件 + DriveNode + v1 revision。

    与上传同款提交/事件语义（教程 reconcile 下个 GET 即可看到文档）；
    校验与父目录解析复用上传的同一套态度（zone 归属、父节点必须是有写权限的文件夹）。
    """
    ensure_zone_roots(db)
    safe_name = Path(name.replace("\\", "/")).name.strip()
    if not safe_name:
        raise HTTPException(status_code=400, detail="document name is required")

    if parent_id is not None:
        parent = drive_repo.get_node(db, parent_id)
        if parent is None:
            raise HTTPException(status_code=404, detail="parent folder not found")
        if parent.kind != DriveNodeKind.folder.value:
            raise HTTPException(status_code=400, detail="parent is not a folder")
        if parent.zone != zone:
            raise HTTPException(status_code=400, detail="parent belongs to a different zone")
        check_write_permission(db, parent, actor_employee_id)
    else:
        parent = drive_repo.get_node_by_path(db, _zone_root_path(db, zone))
        if parent is None:  # pragma: no cover - ensure_zone_roots guarantees it
            raise HTTPException(status_code=404, detail="zone root not found")

    node = create_document(
        db,
        parent=parent,
        name=safe_name,
        content=content,
        doc_type="markdown",
        project_id=project_id if project_id is not None else parent.project_id,
        owner_employee_id=actor_employee_id,
        message="Created document",
        commit=False,
    )
    db.commit()
    db.refresh(node)
    publish_drive("created", node)
    return node


def create_uploaded_file(
    db: Session,
    *,
    zone: str,
    name: str,
    content: bytes,
    parent_id: int | None = None,
    project_id: int | None = None,
    actor_employee_id: int | None = None,
) -> DriveNode:
    """Persist an uploaded Markdown, DOCX, or PDF document and its first revision."""
    ensure_zone_roots(db)
    safe_name = Path(name.replace("\\", "/")).name.strip()
    if not safe_name:
        raise HTTPException(status_code=400, detail="file name is required")
    extension = Path(safe_name).suffix.lower()
    if extension not in UPLOAD_TYPES:
        raise HTTPException(
            status_code=400, detail="only Markdown, DOCX, and PDF files are supported"
        )

    if parent_id is not None:
        parent = drive_repo.get_node(db, parent_id)
        if parent is None:
            raise HTTPException(status_code=404, detail="parent folder not found")
        if parent.kind != DriveNodeKind.folder.value:
            raise HTTPException(status_code=400, detail="parent is not a folder")
        if parent.zone != zone:
            raise HTTPException(status_code=400, detail="parent belongs to a different zone")
        check_write_permission(db, parent, actor_employee_id)
    else:
        parent = drive_repo.get_node_by_path(db, _zone_root_path(db, zone))
        if parent is None:  # pragma: no cover - ensure_zone_roots guarantees it
            raise HTTPException(status_code=404, detail="zone root not found")

    filename = f"{slugify(Path(safe_name).stem, fallback='document')}{extension}"
    path = _unique_path(db, f"{parent.path}/{filename}")
    node = drive_repo.create_node(
        db,
        parent_id=parent.id,
        kind=DriveNodeKind.document.value,
        name=safe_name,
        path=path,
        zone=zone,
        project_id=project_id if project_id is not None else parent.project_id,
        doc_type=UPLOAD_TYPES[extension],
        owner_employee_id=actor_employee_id,
        current_version=1,
    )
    target = abs_path(node)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    drive_repo.create_revision(
        db,
        node_id=node.id,
        version=1,
        sha256=hashlib.sha256(content).hexdigest(),
        author_employee_id=actor_employee_id,
        message="Uploaded file",
    )
    db.commit()
    db.refresh(node)
    publish_drive("created", node)
    return node


def create_project_document(
    db: Session,
    project: Project,
    *,
    doc_type: str,
    title: str,
    content: str,
    owner_employee_id: int | None = None,
    work_session_id: int | None = None,
    message: str | None = None,
) -> DriveNode:
    """Write a workflow artifact into the project's type-mapped subfolder."""
    folders = ensure_project_folders(db, project)
    parent = folders[TYPE_TO_DIR.get(doc_type, "docs")]
    return create_document(
        db,
        parent=parent,
        name=title,
        content=content,
        doc_type=doc_type,
        project_id=project.id,
        owner_employee_id=owner_employee_id,
        work_session_id=work_session_id,
        message=message,
    )


def update_document(
    db: Session,
    node: DriveNode,
    *,
    content: str,
    message: str | None = None,
    actor_employee_id: int | None = None,
) -> DriveNode:
    """PATCH semantics: new revision, bump current_version, rewrite the file."""
    if node.kind == DriveNodeKind.folder.value:
        raise HTTPException(status_code=400, detail="folders have no editable content")
    if delivery_repo.get_active_baseline_document_by_node(db, node.id) is not None:
        raise HTTPException(
            status_code=409,
            detail="baseline documents are immutable; create an approved Change Request",
        )
    if abs_path(node).suffix.lower() not in TEXT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="this document format is read-only")
    check_write_permission(db, node, actor_employee_id)
    node.current_version += 1
    abs_path(node).write_text(content, encoding="utf-8")
    drive_repo.create_revision(
        db,
        node_id=node.id,
        version=node.current_version,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        author_employee_id=actor_employee_id or node.owner_employee_id,
        message=message,
    )
    db.commit()
    db.refresh(node)
    publish_drive("updated", node)
    return node


def create_folder(
    db: Session,
    *,
    zone: str,
    name: str,
    parent_id: int | None = None,
    project_id: int | None = None,
    actor_employee_id: int | None = None,
) -> DriveNode:
    ensure_zone_roots(db)
    if parent_id is not None:
        parent = drive_repo.get_node(db, parent_id)
        if parent is None:
            raise HTTPException(status_code=404, detail="parent folder not found")
        if parent.kind != DriveNodeKind.folder.value:
            raise HTTPException(status_code=400, detail="parent is not a folder")
        if parent.zone != zone:
            raise HTTPException(status_code=400, detail="parent belongs to a different zone")
        check_write_permission(db, parent, actor_employee_id)
    else:
        parent = drive_repo.get_node_by_path(db, _zone_root_path(db, zone))
        if parent is None:  # pragma: no cover - ensure_zone_roots guarantees it
            raise HTTPException(status_code=404, detail="zone root not found")
    path = _unique_path(db, f"{parent.path}/{slugify(name, fallback='folder')}")
    node = drive_repo.create_node(
        db,
        parent_id=parent.id,
        kind=DriveNodeKind.folder.value,
        name=name,
        path=path,
        zone=zone,
        project_id=project_id if project_id is not None else parent.project_id,
        owner_employee_id=actor_employee_id,
    )
    abs_path(node).mkdir(parents=True, exist_ok=True)
    db.commit()
    db.refresh(node)
    publish_drive("created", node)
    return node


# ---- permissions (MVP zone rules, docs/design-v0.3-workspace.md §2) ----


def _is_project_member(db: Session, project_id: int | None, employee_id: int) -> bool:
    if project_id is None:
        return False
    project = project_repo.get_project(db, project_id)
    if project is None:
        return False
    if project.owner_id == employee_id:
        return True
    return any(t.assignee_id == employee_id for t in project_repo.list_tasks(db, project_id))


def check_write_permission(db: Session, node: DriveNode, actor_employee_id: int | None) -> None:
    """Raise 403 when an optional AI employee actor may not write this node."""
    if actor_employee_id is None:
        return
    if org_repo.get_employee(db, actor_employee_id) is None:
        raise HTTPException(status_code=403, detail="employee does not belong to this company")
    if node.zone == DriveZone.projects.value:
        if node.project_id is not None and not _is_project_member(
            db, node.project_id, actor_employee_id
        ):
            raise HTTPException(status_code=403, detail="only project members may write")
        return
    # knowledge / skills / handbook: author writes, everyone reads
    if node.owner_employee_id is not None and node.owner_employee_id != actor_employee_id:
        raise HTTPException(status_code=403, detail="only the author may write this document")

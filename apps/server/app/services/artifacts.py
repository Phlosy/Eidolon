"""Artifact compat layer (v0.3).

The ``artifacts`` table is deprecated and no longer written. Artifacts are now
DriveNodes (kind=document, zone=projects); this module maps them back to the
legacy ArtifactOut shape so old clients keep working
(docs/design-v0.3-workspace.md §2 "Artifact 与 Drive 的关系").
"""

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
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
    task_id: int | None = None,
) -> DriveNode:
    """Write a produced artifact into the project's drive folder (revision v1).

    M2.6（H2）：**产出归属**随内容一起落库（`drive_nodes.task_id`）——
    写入时确定，之后不再猜。`task_id` 缺席时（历史调用点）保持 NULL。
    """
    return drive_service.create_project_document(
        db,
        project,
        doc_type=artifact_type,
        title=title,
        content=content,
        owner_employee_id=author_id,
        work_session_id=work_session_id,
        task_id=task_id,
    )


class ArtifactRefError(ValueError):
    """产物引用无法解析（不是判断，是引用问题）。`http_status` 由调用方翻成 HTTP。"""

    def __init__(self, reason: str, http_status: int = 422) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


def parse_artifact_ref(ref) -> int:
    """把一条引用解析成 DriveNode id（M2.9，W19）。

    只接受两种形式：`12`（int）与 `"drive:12"`。**其它形式一律拒绝** ——
    自由字符串不是引用（"external:https://…" 之类没有可核对的落点）。
    """
    from app.work.contracts import WORK_ORDER_ARTIFACT_REF_PREFIX as PREFIX

    if isinstance(ref, bool):  # bool 是 int 的子类，别让它混进来
        raise ArtifactRefError(f"invalid artifact reference: {ref!r}")
    if isinstance(ref, int):
        return int(ref)
    if isinstance(ref, str) and ref.startswith(PREFIX):
        tail = ref[len(PREFIX) :]
        if tail.isdigit():
            return int(tail)
    raise ArtifactRefError(f"artifact reference must be an id or '{PREFIX}<id>': {ref!r} (W19)")


def resolve_artifact_refs(db: Session, *, company_id: int, refs: list) -> list[dict]:
    """校验交付物引用（M2.9/J2/J3/W19）：必须指向**本公司的真实产物**。

    返回每条引用的**事实**（id / name / doc_type / sha256），供提交记录与读面使用。
    - 引用形式非法 / 产物不存在 / 不是文档 ⇒ **422**
    - 产物属于**别家公司** ⇒ **404**（隔离优先于"参数不对"，J3 同款口径）
    """
    resolved: list[dict] = []
    for ref in refs or []:
        node_id = parse_artifact_ref(ref)
        node = drive_repo.get_node(db, int(node_id))
        if node is None or node.kind != DriveNodeKind.document.value:
            raise ArtifactRefError(f"artifact {node_id} not found", http_status=422)
        if node.company_id is not None and int(node.company_id) != int(company_id):
            # 别家公司的产物：**不存在的口径**（不泄露它存在）
            raise ArtifactRefError(f"artifact {node_id} not found", http_status=404)
        revision = drive_repo.get_revision(db, int(node.id), int(node.current_version))
        resolved.append(
            {
                "artifact_id": int(node.id),
                "name": node.name,
                "doc_type": node.doc_type or "",
                "sha256": revision.sha256 if revision else "",
                "version": int(node.current_version),
                "task_id": int(node.task_id) if node.task_id else None,
            }
        )
    return resolved


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
    identity = get_request_identity()
    if identity is not None:
        stmt = stmt.where(DriveNode.company_id == identity.company_id)
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

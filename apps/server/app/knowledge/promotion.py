"""Knowledge promotion: proposal / review / drive 物化. See docs/architecture.md §6.3.

Private knowledge is isolated by default; promotion requires Proposal + Review.
The target scope is stored on the item's ``proposed_scope`` column (proposals
have no dedicated table in the MVP).

K1（docs/talent-ecosystem-plan.md §3 / vision §5）评审通过时把条目物化成 drive
markdown 文档：company → handbook 区根目录；department → knowledge 区的部门子
文件夹（``dept-<id>``，item 没有 department_id 时回落到条目 owner 的部门，再
没有则落在 knowledge 区根）。双向回链（不加迁移，复用现有字段）：

- ``KnowledgeItem.sources`` 追加 ``drive_node://<id>``（沿用 ``task://`` /
  ``learning_record://`` 的 ``<type>://<id>`` 惯例）；
- 文档正文头部携带 ``knowledge_item://<id>`` 注释标记，反向可追溯。

事务/失败语义：approve 先把 scope/status 落进会话（flush），随后
``create_markdown_document`` 的 commit 把**条目变更与文档行/版本一起**提交
——物化失败则异常外抛、晋升不落库，不会出现"晋升成功但文档缺失"的静默不一致；
回链 sources 是随后一次独立小 commit，即便它失败，文档内的
``knowledge_item://`` 反向回链仍在，状态可见可追。物化幂等：sources 里已有
有效 ``drive_node://`` 回链时直接复用该节点，不重复建文档。

已知技术债：review 没有审批者权限模型，任何登录用户（甚至无审批角色约束的
调用方）都能评审任何提案；权限模型留待后续阶段补。
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.drive import DriveNode
from app.models.enums import DriveNodeKind, DriveZone, KnowledgeScope, KnowledgeStatus
from app.models.knowledge import KnowledgeItem
from app.models.organization import Department, Employee
from app.repositories import drive as drive_repo
from app.services import drive as drive_service

#: KnowledgeItem.sources 里指向物化文档的回链前缀（``drive_node://<id>``）。
DRIVE_SOURCE_PREFIX = "drive_node://"
#: 物化文档正文里反向指向知识条目的标记（``knowledge_item://<id>``）。
ITEM_BACKLINK_PREFIX = "knowledge_item://"


def propose(db: Session, item: KnowledgeItem, target_scope: str) -> KnowledgeItem:
    target = KnowledgeScope(target_scope).value
    if target == KnowledgeScope.private.value:
        raise HTTPException(status_code=400, detail="cannot propose promotion to private scope")
    if item.status == KnowledgeStatus.proposed.value:
        raise HTTPException(status_code=409, detail="knowledge item already has a pending proposal")
    if item.scope == target:
        raise HTTPException(status_code=409, detail="knowledge item already at target scope")
    item.status = KnowledgeStatus.proposed.value
    item.proposed_scope = target
    db.commit()
    db.refresh(item)
    bus.publish(
        "knowledge.proposed",
        {"id": item.id, "from": item.scope, "target_scope": target},
        actor_employee_id=item.owner_employee_id,
    )
    return item


def review(db: Session, item: KnowledgeItem, approve: bool) -> KnowledgeItem:
    """审批晋升提案；approve 通过时同步物化到 drive（见模块 docstring 的事务语义）。"""
    if item.status != KnowledgeStatus.proposed.value or not item.proposed_scope:
        raise HTTPException(status_code=409, detail="knowledge item has no pending proposal")
    if approve:
        item.scope = item.proposed_scope
        item.status = KnowledgeStatus.active.value
        item.proposed_scope = None
        db.flush()
        node = _materialize_to_drive(db, item)
        link = f"{DRIVE_SOURCE_PREFIX}{node.id}"
        sources = list(item.sources or [])
        if link not in sources:
            item.sources = [*sources, link]
        db.commit()
        db.refresh(item)
        bus.publish(
            "knowledge.promoted",
            {"id": item.id, "scope": item.scope, "drive_node_id": node.id},
            actor_employee_id=item.owner_employee_id,
        )
    else:
        item.status = KnowledgeStatus.rejected.value
        item.proposed_scope = None
        db.commit()
        db.refresh(item)
    return item


# ---- drive 物化 ----


def _item_company_id(db: Session, item: KnowledgeItem) -> int | None:
    """从条目自身解析归属公司（物化跑在系统上下文，不依赖 request identity）。"""
    if item.owner_employee_id is not None:
        owner = db.get(Employee, item.owner_employee_id)
        if owner is not None:
            return owner.company_id
    if item.department_id is not None:
        department = db.get(Department, item.department_id)
        if department is not None:
            return department.company_id
    return None


def _existing_materialized_node(db: Session, item: KnowledgeItem) -> DriveNode | None:
    for source in item.sources or []:
        if isinstance(source, str) and source.startswith(DRIVE_SOURCE_PREFIX):
            try:
                node_id = int(source.removeprefix(DRIVE_SOURCE_PREFIX))
            except ValueError:
                continue
            node = drive_repo.get_node(db, node_id)
            if node is not None:
                return node
    return None


def _ensure_department_folder(
    db: Session, zone_root: DriveNode, department: Department, company_id: int | None
) -> DriveNode:
    """knowledge 区下的部门子文件夹（``dept-<id>``，显示名用部门名）；create_node
    碰撞安全 + 回查兜底幂等，不 commit（由上层 create_markdown_document 一并提交）。"""
    path = f"{zone_root.path}/dept-{department.id}"
    node = drive_repo.get_node_by_path(db, path)
    if node is None:
        fields: dict = dict(
            parent_id=zone_root.id,
            kind=DriveNodeKind.folder.value,
            name=department.name,
            path=path,
            zone=zone_root.zone,
        )
        if company_id is not None:
            # 显式公司归属；为 None 时交给 create_node 从 parent 继承
            fields["company_id"] = company_id
        node = drive_repo.create_node(db, **fields)
        drive_service.abs_path(node).mkdir(parents=True, exist_ok=True)
    return node


def _materialize_to_drive(db: Session, item: KnowledgeItem) -> DriveNode:
    """把已通过评审的条目物化成 drive markdown 文档（幂等：已物化则复用）。

    写路径只走 drive service/repo 的公开入口（zone 根用 ensure_zone_roots，
    目录用 drive_repo.create_node，文档用 create_markdown_document——它内部
    commit 并 publish_drive）。actor_employee_id=None：系统物化场景跳过
    作者写权限检查（knowledge/handbook 区语义是"作者可写、全公司可读"，
    这里落盘的是已发布知识，不属于任何个人作者）。
    """
    existing = _existing_materialized_node(db, item)
    if existing is not None:
        return existing

    company_id = _item_company_id(db, item)
    drive_service.ensure_zone_roots(db, company_id)

    if item.scope == KnowledgeScope.company.value:
        zone = DriveZone.handbook.value
    else:
        zone = DriveZone.knowledge.value
    zone_root = drive_repo.get_node_by_path(db, drive_service.zone_root_path(db, zone, company_id))
    if zone_root is None:  # pragma: no cover - ensure_zone_roots guarantees it
        raise HTTPException(status_code=500, detail=f"drive zone root not found: {zone}")

    parent = zone_root
    if item.scope == KnowledgeScope.department.value:
        department_id = item.department_id
        if department_id is None and item.owner_employee_id is not None:
            owner = db.get(Employee, item.owner_employee_id)
            department_id = owner.department_id if owner is not None else None
        if department_id is not None:
            department = db.get(Department, department_id)
            if department is not None:
                parent = _ensure_department_folder(db, zone_root, department, company_id)
        # 没有部门信息时的兜底：直接落在 knowledge 区根（parent 已是 zone_root）

    backlink = f"{ITEM_BACKLINK_PREFIX}{item.id}"
    content = (
        f"# {item.title}\n\n"
        f"<!-- {backlink} -->\n"
        f"> 由知识条目 #{item.id} 晋升物化（scope: {item.scope}）\n\n"
        f"- **topic**: {item.topic}\n"
        f"- **confidence**: {item.confidence}\n"
        f"- **sources**: {', '.join(str(s) for s in item.sources or []) or '（无）'}\n\n"
        f"{item.content}\n"
    )
    return drive_service.create_markdown_document(
        db,
        zone=zone,
        name=item.title,
        content=content,
        parent_id=parent.id,
        actor_employee_id=None,
    )

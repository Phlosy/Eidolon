"""RoleContext —— 履职上下文投影 + Role Resource Index 解析（M2.2，W27 / W41）。

设计 §5/§6 的落地。两条纪律：

1. **派生读模型，不落表**（M2-ADR-4）：`build_role_context()` 每次从
   任职时间轴 + 职位定义 + 岗位画像 + 公司配置**现算**。派生态一旦落库必然与任职漂移，
   `WorkforceStatusResolver` 已经证明"读时派生"是可行且更诚实的口径。
2. **只给引用，不给分数**（C5 / W7）：RoleContext 携带
   `expectations` 的**引用**（competency code + required/preferred + critical），
   分值留在岗位画像里按需另读。给它塞数字就是"任命时发一张成绩单"。

Role Resource Index（§6）**是指针，不是内容**（C6 / W41）：每条 `ref` 必须解析到
既有内容表（`knowledge_items` / `drive_nodes`）或既有配置（`companies.settings`），
否则如实报告"目标尚不存在" —— 「给新 CEO 一份阅读清单」不能变成第二套文档系统。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import DriveZone, KnowledgeScope, KnowledgeStatus
from app.models.knowledge import KnowledgeItem
from app.models.organization import Company, Employee
from app.models.position import PositionDefinition, PositionDefinitionResource
from app.models.project import Project
from app.models.project_delivery import ProjectRequirement
from app.repositories import drive as drive_repo
from app.services import position_profile as profile_service
from app.work import authority as authority_service
from app.work import contracts as C

#: 项目处于这些状态时算"仍在进行"（与 `progress` 口径一致，不另立一套）
LIVE_PROJECT_STATUSES = (
    "requested",
    "planning",
    "in_progress",
    "in_review",
    "waiting_for_management",
)


class ResourceResolution(StrEnum):
    """`ref` 的解析结果（派生，不落库）。"""

    #: 指向既有内容/配置（`resolution` 里给出指针）
    resolved = "resolved"
    #: 按设计不指向内容（`skill_hint` 只是一个"建议学的技能名"）
    advisory = "advisory"
    #: 指针目标尚不存在（例如公司还没发布任何手册/知识）
    missing = "missing"


@dataclass(frozen=True)
class ResolvedRoleResource:
    """Role Resource Index 的一项 + 它的解析结果。"""

    kind: C.RoleResourceKind
    ref: str
    note: str
    required: bool
    resolution: ResourceResolution
    pointer: str = ""  # "knowledge_item:12" / "drive_node:7" / "company_setting:work_routing"

    def to_contract(self) -> C.RoleResource:
        return C.RoleResource(kind=self.kind, ref=self.ref, note=self.note, required=self.required)


# ---------------------------------------------------------------------------
# 派生（只读）
# ---------------------------------------------------------------------------


def _responsibility_titles(definition: PositionDefinition | None) -> tuple[str, ...]:
    """`position_definitions.responsibilities` → 标题元组。

    该列是 JSON list，历史上出现过两种形态（纯字符串 / `{"title": …}`），
    两种都接受；空值一律跳过，**不**编造占位标题。
    """
    if definition is None:
        return ()
    titles: list[str] = []
    for item in definition.responsibilities or []:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("title") or item.get("name") or "").strip()
        else:  # pragma: no cover - 防御：脏数据不进契约
            text = ""
        if text:
            titles.append(text)
    return tuple(titles)


def _expectation_refs(
    db: Session, definition: PositionDefinition | None
) -> tuple[C.PositionExpectationRef, ...]:
    """ACTIVE 岗位画像 → 期望**引用**（不带分值，C5）。"""
    if definition is None or definition.id is None:
        return ()
    version = profile_service.active_profile(db, int(definition.id))
    if version is None:
        return ()
    from app.models.competency import CompetencyDefinition

    rows = profile_service.requirements_for(db, version)
    definition_ids = {int(row.competency_definition_id) for row in rows}
    codes: dict[int, str] = {}
    if definition_ids:
        codes = {
            int(row.id): str(row.code)
            for row in db.scalars(
                select(CompetencyDefinition).where(CompetencyDefinition.id.in_(definition_ids))
            )
        }
    refs: list[C.PositionExpectationRef] = []
    for row in rows:
        code = codes.get(int(row.competency_definition_id))
        if not code:
            continue  # 能力目录里没有的引用不编造
        refs.append(
            C.PositionExpectationRef(
                competency_code=code,
                requirement_type=row.requirement_type or "required",
                critical=bool(row.critical),
            )
        )
    return tuple(refs)


def _company_policy_keys(company: Company | None) -> tuple[str, ...]:
    """公司配置里**存在**的策略键（事实引用；值按需另读各自的策略端点）。"""
    if company is None:
        return ()
    keys = set((company.settings or {}).keys())
    keys.update({C.RESPONSIBILITY_SETTINGS_KEY, C.WORK_MODE_SETTINGS_KEY})
    return tuple(sorted(keys))


def _live_project_ids(db: Session, company_id: int) -> tuple[int, ...]:
    rows = db.scalars(
        select(Project.id)
        .where(Project.company_id == int(company_id), Project.status.in_(LIVE_PROJECT_STATUSES))
        .order_by(Project.id.desc())
    )
    return tuple(int(row) for row in rows)


def knowledge_scopes_for() -> tuple[str, ...]:
    """此人可以检索的知识作用域 —— 与 K1 的检索实现同源（不另立一套可见性规则）。"""
    return (
        KnowledgeScope.private.value,
        KnowledgeScope.department.value,
        KnowledgeScope.company.value,
    )


def build_role_context(
    db: Session,
    employee: Employee,
    *,
    at: datetime | None = None,
) -> C.RoleContext:
    """构建履职上下文（只读、现算、不含分值）。"""
    actor = authority_service.resolve_actor_authority(db, int(employee.id), at=at)
    definition = (
        db.get(PositionDefinition, int(actor.position_definition_id))
        if actor.position_definition_id is not None
        else None
    )
    company = db.get(Company, int(employee.company_id)) if employee.company_id else None

    return C.RoleContext(
        person_id=int(employee.person_id) if employee.person_id is not None else None,
        employee_id=int(employee.id),
        position_definition_id=actor.position_definition_id,
        position_code=actor.position_code,
        department_id=actor.department_id or employee.department_id,
        responsibilities=_responsibility_titles(definition),
        authority=actor.contract_grants,
        expectations=_expectation_refs(db, definition),
        advisory_scope=(
            tuple(str(item) for item in (definition.advisory_scope or [])) if definition else ()
        ),
        resource_index=tuple(item.to_contract() for item in resolve_role_resources(db, employee)),
        direct_reports=authority_service.direct_report_employee_ids(db, actor),
        company_policy_keys=_company_policy_keys(company),
        current_project_ids=(
            _live_project_ids(db, actor.company_id) if employee.company_id else ()
        ),
        knowledge_scopes=knowledge_scopes_for(),
    )


def live_projects_for_role(db: Session, employee: Employee, *, limit: int = 20) -> list[dict]:
    """当前进行中的项目 + 该项目中"这个职位需要关心"的线索（只读事实）。

    刻意只给标题与状态计数：需求细节属于项目读面（`/projects/{id}`），
    在 RoleContext 里复述一遍就会长出第二套项目摘要。
    """
    if not employee.company_id:
        return []
    rows = db.scalars(
        select(Project)
        .where(
            Project.company_id == int(employee.company_id),
            Project.status.in_(LIVE_PROJECT_STATUSES),
        )
        .order_by(Project.id.desc())
        .limit(max(0, int(limit)))
    )
    briefs: list[dict] = []
    for project in rows:
        requirement_count = len(
            db.scalars(
                select(ProjectRequirement.id).where(
                    ProjectRequirement.project_id == int(project.id)
                )
            ).all()
        )
        briefs.append(
            {
                "project_id": int(project.id),
                "name": project.name,
                "status": project.status,
                "work_mode": project.work_mode,
                "requirement_count": requirement_count,
            }
        )
    return briefs


# ---------------------------------------------------------------------------
# Role Resource Index：声明 + 解析（指针，不是内容）
# ---------------------------------------------------------------------------


def declare_role_resource(
    db: Session,
    *,
    position_definition_id: int,
    kind: C.RoleResourceKind,
    ref: str,
    note: str = "",
    required: bool = False,
    commit: bool = True,
) -> PositionDefinitionResource:
    """声明一条资源引用（幂等：同 (职位, kind, ref) 只有一条）。

    只接受 **ref 文本**；内容永远住在它要解析到的那张表里（W41 / C6）。
    """
    reference = ref.strip()
    if not reference:
        raise C.WorkContractError("role resource ref must not be empty")
    existing = db.scalar(
        select(PositionDefinitionResource).where(
            PositionDefinitionResource.position_definition_id == int(position_definition_id),
            PositionDefinitionResource.kind == kind.value,
            PositionDefinitionResource.ref == reference,
        )
    )
    if existing is not None:
        return existing
    row = PositionDefinitionResource(
        position_definition_id=int(position_definition_id),
        kind=kind.value,
        ref=reference,
        note=note,
        required=bool(required),
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
    return row


def _resolve_one(
    db: Session, row: PositionDefinitionResource, company: Company | None
) -> ResolvedRoleResource:
    """把一条 `ref` 解析到既有内容/配置（不命中就如实报告 `missing`）。"""
    kind = C.RoleResourceKind(row.kind)
    base = {
        "kind": kind,
        "ref": row.ref,
        "note": row.note,
        "required": bool(row.required),
    }
    if kind is C.RoleResourceKind.skill_hint:
        # 按设计不指向内容：它只是"建议学的技能名"，本身没有载体（W41）
        return ResolvedRoleResource(resolution=ResourceResolution.advisory, **base)
    if kind is C.RoleResourceKind.policy:
        if company is not None and row.ref in (company.settings or {}):
            return ResolvedRoleResource(
                resolution=ResourceResolution.resolved,
                pointer=f"company_setting:{row.ref}",
                **base,
            )
        known_policy_keys = {C.RESPONSIBILITY_SETTINGS_KEY, C.WORK_MODE_SETTINGS_KEY}
        if company is not None and row.ref in known_policy_keys:
            # 这两个键即使公司还没显式写过也算"存在"（有默认值，读面能回答）
            return ResolvedRoleResource(
                resolution=ResourceResolution.resolved,
                pointer=f"company_setting:{row.ref}",
                **base,
            )
        return ResolvedRoleResource(resolution=ResourceResolution.missing, **base)
    if kind is C.RoleResourceKind.knowledge_topic:
        item = db.scalar(
            select(KnowledgeItem)
            .where(
                KnowledgeItem.topic == row.ref,
                KnowledgeItem.status == KnowledgeStatus.active.value,
                KnowledgeItem.scope.in_(
                    [KnowledgeScope.department.value, KnowledgeScope.company.value]
                ),
            )
            .order_by(KnowledgeItem.id)
        )
        if item is None:
            return ResolvedRoleResource(resolution=ResourceResolution.missing, **base)
        return ResolvedRoleResource(
            resolution=ResourceResolution.resolved, pointer=f"knowledge_item:{int(item.id)}", **base
        )
    # handbook / playbook：解析到 Drive 的 handbook / knowledge 区（目录或文档都算命中）
    zones = (DriveZone.handbook.value, DriveZone.knowledge.value)
    node = None
    for zone in zones:
        node = db.scalar(
            select(drive_repo.DriveNode)
            .where(
                drive_repo.DriveNode.zone == zone,
                (drive_repo.DriveNode.path == row.ref) | (drive_repo.DriveNode.name == row.ref),
            )
            .order_by(drive_repo.DriveNode.id)
        )
        if node is not None:
            break
    if node is None:
        return ResolvedRoleResource(resolution=ResourceResolution.missing, **base)
    return ResolvedRoleResource(
        resolution=ResourceResolution.resolved, pointer=f"drive_node:{int(node.id)}", **base
    )


def resolve_role_resources(db: Session, employee: Employee) -> tuple[ResolvedRoleResource, ...]:
    """这个人当前职位的资源清单（已解析到既有内容/配置）。"""
    actor = authority_service.resolve_actor_authority(db, int(employee.id))
    if actor.position_definition_id is None:
        return ()
    rows = db.scalars(
        select(PositionDefinitionResource)
        .where(
            PositionDefinitionResource.position_definition_id == int(actor.position_definition_id)
        )
        .order_by(PositionDefinitionResource.required.desc(), PositionDefinitionResource.id)
    )
    company = db.get(Company, int(employee.company_id)) if employee.company_id else None
    return tuple(_resolve_one(db, row, company) for row in rows)


def role_resource_count(db: Session, position_definition_id: int) -> int:
    return len(
        db.scalars(
            select(PositionDefinitionResource.id).where(
                PositionDefinitionResource.position_definition_id == int(position_definition_id)
            )
        ).all()
    )

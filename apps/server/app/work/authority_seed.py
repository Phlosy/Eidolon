"""M2.2 冷启动种子：默认管理授权 / 默认职责范围 / 默认资源清单（v41 之后）。

三条纪律：

1. **default-deny 需要默认声明**：`position_authority_grants` 空 ⇒ 一切管理动作都被拒绝
   （这是设计，不是 bug）。但新公司必须能开张，所以官方给一份**最小、可见、可改**的
   默认授权：CEO 能接收工作并组织公司，PM 能在交付内派活与要求返工，QA 能要求返工，
   研究/工程岗**没有任何管理授权**（他们做事，不管人）。
2. **幂等**：按 `(职位定义, 授权, 作用域, scope_ref)` 去重（`grant_authority` 自带幂等）。
   每次启动重跑不产生第二条。
3. **金额来自政策**（M1 纪律）：`spend_credits` 的上限取
   `settings.authority_default_spend_limit`，**不**在代码里硬编码数字。政策调高后
   不会自动放大已存在的授权 —— 要改授权就得显式 revoke + grant（授权不该悄悄扩张）。

这份种子**只覆盖公司已有的内置职位定义**；公司自建职位需要自己声明授权
（玩家面写端点不在 M2.2 范围，服务层入口是 `authority.grant_authority`）。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.enums import AuthorityScopeKind, TaskKind
from app.models.position import (
    PositionAuthorityGrant,
    PositionDefinition,
    PositionDefinitionResource,
)
from app.repositories import position as position_repo
from app.work import authority as authority_service
from app.work import contracts as C
from app.work import role_context as role_context_service

logger = logging.getLogger(__name__)

#: 冷启动默认授权：职位 code → 授权类型（全部 `company` 作用域）。
#:
#: `department` / `direct_reports` 作用域**不在默认种子里**：它们要依赖
#: `position_slots.manager_slot_id` 这条汇报线存在才有意义，而新公司开局并没有
#: 汇报线。公司把汇报线搭起来之后可以按需把授权收窄（服务层 revoke + grant）。
DEFAULT_AUTHORITY: dict[str, tuple[C.AuthorityKind, ...]] = {
    "ceo": (
        C.AuthorityKind.create_project,
        C.AuthorityKind.delegate_management,
        C.AuthorityKind.assign_task,
        C.AuthorityKind.request_rework,
        C.AuthorityKind.accept_delivery,
        C.AuthorityKind.approve_hiring,
        C.AuthorityKind.spend_credits,
        C.AuthorityKind.assign_position,
        C.AuthorityKind.release_position,
        C.AuthorityKind.offboard,
    ),
    "product_manager": (C.AuthorityKind.assign_task, C.AuthorityKind.request_rework),
    "qa_engineer": (C.AuthorityKind.request_rework,),
    # 研究 / 工程：**没有管理授权**（他们执行工作，不管理组织）
    "researcher": (),
    "engineer": (),
}

#: 冷启动默认"通常做什么工作"（advisory only，W5/W12 —— 绝不是工作边界）。
DEFAULT_ADVISORY_SCOPE: dict[str, tuple[str, ...]] = {
    "ceo": (TaskKind.order_review.value, TaskKind.final_review.value),
    "product_manager": (TaskKind.order_review.value, TaskKind.planning.value),
    "researcher": (TaskKind.research.value,),
    "engineer": (TaskKind.development.value,),
    "qa_engineer": (TaskKind.testing.value,),
}

#: 冷启动资源清单（**指针**：内容住在 knowledge_items / drive_nodes / companies.settings）。
#:
#: `handbook` 指向 Drive 的 handbook 区根目录（`ensure_zone_roots` 建的，开局就存在）；
#: `policy` 指向公司配置里**有默认值**的策略键。两者都能立即解析，
#: 而公司知识/手册一旦发布，同一个指针就会指向真实内容（W41 / C6）。
DEFAULT_RESOURCES: dict[str, tuple[tuple[C.RoleResourceKind, str, str, bool], ...]] = {
    "ceo": (
        (
            C.RoleResourceKind.policy,
            C.RESPONSIBILITY_SETTINGS_KEY,
            "谁负责接收公司工作（Work Intake 责任路由）",
            True,
        ),
        (
            C.RoleResourceKind.policy,
            C.WORK_MODE_SETTINGS_KEY,
            "公司默认工作模式（guided / managed）",
            True,
        ),
        (
            C.RoleResourceKind.handbook,
            "handbook",
            "公司制度与知识手册：公司发布知识后这里会指向真实内容",
            False,
        ),
    ),
    "product_manager": (
        (C.RoleResourceKind.handbook, "handbook", "公司制度与知识手册", False),
        (C.RoleResourceKind.policy, C.WORK_MODE_SETTINGS_KEY, "公司默认工作模式", False),
    ),
    "researcher": ((C.RoleResourceKind.handbook, "handbook", "公司制度与知识手册", False),),
    "engineer": ((C.RoleResourceKind.handbook, "handbook", "公司制度与知识手册", False),),
    "qa_engineer": ((C.RoleResourceKind.handbook, "handbook", "公司制度与知识手册", False),),
}


def _definitions_by_code(db: Session, company_id: int) -> dict[str, PositionDefinition]:
    return {
        definition.code: definition for definition in position_repo.list_definitions(db, company_id)
    }


def seed_default_authority(db: Session, company_id: int | None = None) -> dict[str, int]:
    """把冷启动默认授权 / 职责范围 / 资源清单种下（幂等）。

    返回本次实际新增的计数（重跑应为全 0）。
    """
    from app.repositories import organization as org_repo

    company = (
        org_repo.get_company(db, int(company_id))
        if company_id is not None
        else org_repo.get_default_company(db)
    )
    if company is None:
        return {"grants": 0, "resources": 0, "advisory_scope": 0}

    definitions = _definitions_by_code(db, int(company.id))
    if not definitions:
        # 没有任何职位定义时无事可做（lifecycle seed 还没跑）
        return {"grants": 0, "resources": 0, "advisory_scope": 0}

    grants_created = 0
    resources_created = 0
    scope_updated = 0

    for code, kinds in DEFAULT_AUTHORITY.items():
        definition = definitions.get(code)
        if definition is None:
            continue
        for kind in kinds:
            already = db.scalar(
                select(PositionAuthorityGrant).where(
                    PositionAuthorityGrant.position_definition_id == int(definition.id),
                    PositionAuthorityGrant.authority_kind == kind.value,
                    PositionAuthorityGrant.scope_kind == AuthorityScopeKind.company.value,
                    PositionAuthorityGrant.scope_ref == 0,
                    PositionAuthorityGrant.effective_to.is_(None),
                )
            )
            if already is not None:
                continue  # 幂等：已有生效授权就不动（也绝不"顺手"改它的额度）
            authority_service.grant_authority(
                db,
                position_definition_id=int(definition.id),
                kind=kind,
                scope_kind=AuthorityScopeKind.company,
                scope_ref=0,
                max_amount=(
                    int(settings.authority_default_spend_limit)
                    if kind in C.AMOUNT_BEARING_AUTHORITIES
                    else None
                ),
                note="cold start default",
                commit=False,
            )
            grants_created += 1

        advisory = DEFAULT_ADVISORY_SCOPE.get(code)
        if advisory and list(definition.advisory_scope or []) != list(advisory):
            definition.advisory_scope = list(advisory)
            scope_updated += 1

        for kind, ref, note, required in DEFAULT_RESOURCES.get(code, ()):
            existing = db.scalar(
                select(PositionDefinitionResource).where(
                    PositionDefinitionResource.position_definition_id == int(definition.id),
                    PositionDefinitionResource.kind == kind.value,
                    PositionDefinitionResource.ref == ref,
                )
            )
            if existing is not None:
                continue
            role_context_service.declare_role_resource(
                db,
                position_definition_id=int(definition.id),
                kind=kind,
                ref=ref,
                note=note,
                required=required,
                commit=False,
            )
            resources_created += 1

    db.commit()
    if grants_created or resources_created or scope_updated:
        logger.info(
            "M2.2 冷启动种子：授权 +%d / 资源 +%d / 职责范围 %d",
            grants_created,
            resources_created,
            scope_updated,
        )
    return {
        "grants": grants_created,
        "resources": resources_created,
        "advisory_scope": scope_updated,
    }

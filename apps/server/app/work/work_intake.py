"""Work Intake 责任路由（M2.1，D1 / M2-ADR-11，W32 / W34）。

**系统不选人，系统只回答"公司把这个责任交给哪个职位、现在谁占着它"。**

路由链（设计 §11.5）：

```text
Company.settings["work_routing"]["work_intake"]   ← 公司可配（缺省 = ceo）
        ↓  解析成 PositionDefinition.code
PositionDefinition（公司自建优先；没有就回落到默认 code）
        ↓
PositionSlot → 生效中的 PRIMARY PositionAssignment（repositories/position.py 唯一查询器）
        ↓
Employee（还要可用：lifecycle_status == active）
        ↓
Project.management_* 快照
```

三条硬纪律：

1. **找不到就如实说找不到**（`no_position` / `no_incumbent` / `incumbent_unavailable`）——
   项目进 `waiting_for_management`，由 Owner 手动处理或去任命。
   绝不"随便挑一个员工"（那违反 `Agent makes decisions.`）。
2. **多人在任不是歧义**：公司已经指定了**职位**，该职位的每一位在任者都**按定义**承担这份责任。
   所以这里取 `effective_from` 最早的那一位，并把**全部在任者**放进
   `candidate_employee_ids` 供审计 —— 这不构成"系统替公司选人"（W1）。
3. **只读**：本模块不做任何写入，也不发事件；它回答事实，不产生决策。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.models.enums import LifecycleStatus, ResponsibilityKind
from app.models.organization import Company
from app.repositories import organization as org_repo
from app.repositories import position as position_repo
from app.work import contracts as C


class WorkIntakeStatus(StrEnum):
    """路由结果状态（**派生，不落库** —— 与 T2 的 `MarketState` 同一纪律）。"""

    routed = "routed"
    #: 公司没有这个职位定义（既没自建，也没有内置模板）
    no_position = "no_position"
    #: 职位在，但没有任何生效 PRIMARY 任职
    no_incumbent = "no_incumbent"
    #: 有人在任，但当前不可工作（未入职完成 / 停用 / 离职中）
    incumbent_unavailable = "incumbent_unavailable"

    @property
    def is_routed(self) -> bool:
        return self is WorkIntakeStatus.routed


#: 覆盖 `RESPONSIBILITY_DEFAULTS` 的公司配置读取（缺省 -> (默认 code, False)）
def configured_position_code(company: Company | None, responsibility: ResponsibilityKind) -> str:
    """公司为该责任配置的职位 code（缺省 = `RESPONSIBILITY_DEFAULTS`）。"""
    return configured_position_code_and_source(company, responsibility)[0]


def configured_position_code_and_source(
    company: Company | None, responsibility: ResponsibilityKind
) -> tuple[str, bool]:
    """返回 `(position_code, is_explicit_company_override)`。

    `is_explicit` 很重要：它让"默认是 CEO"与"公司把 Work Intake 交给了 COO"在
    读面上可区分 —— 用户能看见自己是否改过配置。
    """
    default = C.RESPONSIBILITY_DEFAULTS[responsibility]
    raw = (getattr(company, "settings", None) or {}).get(C.RESPONSIBILITY_SETTINGS_KEY) or {}
    configured = str(raw.get(responsibility.value) or "").strip()
    if not configured:
        return default, False
    return configured, configured != default


@dataclass(frozen=True)
class WorkIntakeResolution:
    """Work Intake 路由结果（只读投影，不落库）。"""

    responsibility: ResponsibilityKind
    status: WorkIntakeStatus
    configured_position_code: str
    default_position_code: str
    #: 公司是否显式改写默认（用于 UI 区分"默认 CEO"与"我们配成 COO"）
    is_configured: bool = False
    position_definition_id: int | None = None
    slot_id: int | None = None
    employee_id: int | None = None
    person_id: int | None = None
    #: 该职位全部在任者（审计用；第一个是本次路由选中的那位）
    candidate_employee_ids: tuple[int, ...] = ()
    #: 公司 Owner（没有负责人时由"人"手动处理，而不是系统代管）
    owner_user_id: int | None = None
    reason: str = ""

    @property
    def is_routed(self) -> bool:
        return self.status.is_routed

    @property
    def needs_owner_attention(self) -> bool:
        return not self.is_routed


def resolve_work_intake(db: Session, company: Company) -> WorkIntakeResolution:
    """解析"现在谁负责接收这个公司的工作"（只读，无副作用）。

    调用方（`services/projects.py`）只在结果 `is_routed` 时把项目交给它；
    否则项目进 `waiting_for_management`，并且**不做任何规划**（W34）。
    """
    responsibility = ResponsibilityKind.work_intake
    code, is_configured = configured_position_code_and_source(company, responsibility)
    default_code = C.RESPONSIBILITY_DEFAULTS[responsibility]
    owner_user_id = org_repo.owner_user_id(db, int(company.id))

    common = {
        "responsibility": responsibility,
        "configured_position_code": code,
        "default_position_code": default_code,
        "is_configured": is_configured,
        "owner_user_id": owner_user_id,
    }

    definition = position_repo.get_definition_by_code(db, code, company_id=int(company.id))
    if definition is None:
        return WorkIntakeResolution(
            status=WorkIntakeStatus.no_position,
            reason=(
                f"公司没有「{code}」这个职位，也没有人承担 Work Intake 责任。"
                "请先建立该职位并任命，或由 Owner 手动处理。"
            ),
            **common,
        )

    candidates = position_repo.employees_in_position(
        db, definition_code=code, company_id=int(company.id)
    )
    if not candidates:
        return WorkIntakeResolution(
            status=WorkIntakeStatus.no_incumbent,
            position_definition_id=int(definition.id),
            reason=(
                f"「{definition.name}」职位当前没有在任者（没有生效主职）。"
                "请任命该职位，或由 Owner 手动处理。"
            ),
            **common,
        )

    # 多人在任按 effective_from 取最早（repo 已排序）；全部在任者随结果上报，便于审计。
    for employee_id in candidates:
        employee = org_repo.get_employee(db, employee_id)
        if employee is None:  # pragma: no cover - 防御：任职指向缺失员工
            continue
        if employee.lifecycle_status != LifecycleStatus.active.value:
            continue
        slot_ids = [
            int(row.position_slot_id)
            for row in position_repo.active_assignments(db, employee_id)
            if row.position_slot_id is not None
        ]
        return WorkIntakeResolution(
            status=WorkIntakeStatus.routed,
            position_definition_id=int(definition.id),
            slot_id=slot_ids[0] if slot_ids else None,
            employee_id=int(employee.id),
            person_id=int(employee.person_id) if employee.person_id is not None else None,
            candidate_employee_ids=tuple(int(c) for c in candidates),
            reason=f"Work Intake 由「{definition.name}」承担。",
            **common,
        )

    return WorkIntakeResolution(
        status=WorkIntakeStatus.incumbent_unavailable,
        position_definition_id=int(definition.id),
        candidate_employee_ids=tuple(int(c) for c in candidates),
        reason=(
            f"「{definition.name}」有在任者但当前不可工作（未完成入职 / 已停用 / 离职中）。"
            "请先让其就绪，或由 Owner 手动处理。"
        ),
        **common,
    )

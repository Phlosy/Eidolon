"""M2.9 **WorkOrder → Project 绑定边**（设计 §11.2，W23 / WO1–WO6）。

一句话：**连接商业需求与执行载体，不把 WorkOrder 变成执行图**（W23）。

```text
WorkOrder ACCEPTED（M1 的经济事实，不动它的状态机）
        ↓ 事件 `work_order.accepted`
桥：route_accepted_order()  → 解析公司的 Work Intake 责任人（M2.1/M2.4 规则）
                           → 记一条 `routed`（**系统投递**，不是决定）
        ↓ 管理层决定（人类 API / Manager Agent）
      bind_project()       → 把某个 Project 绑上（写指针 + `bound` 行）
      decline_binding()    → 明确不接（理由必填 + `declined` 行）
```

四条边界（都有锚点与反例注入）：

1. **只加一条边**（WO1/J5）：WorkOrder 的 12 个状态一个不加；桥不碰状态机。
2. **绑定是管理动作**（WO2/WO5）：系统只投递与记录；它**不创建、不规划** Project
   （W2/W34），也不替谁选执行载体（W1）。
3. **引用受校验**（WO3/WO4）：`project_id` 必须存在且属于承接公司（跨公司 404）；
   交付物引用必须指向本公司的真实产物（W19）。
4. **不碰钱**（WO6）：验收/结算/账本仍然走 M1 的既有路径，桥一行都不写。

`work_orders.project_id` 是**当前绑定的指针**；`work_order_project_links` 是**决定的历史**
（J1 要"绑定或显式拒绝两条路径都有记录"）。两者只由本模块写 —— 同一条事实一个写入者。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.economy import WorkOrder, WorkOrderProjectLink
from app.models.enums import WorkOrderStatus
from app.models.organization import Employee
from app.repositories import organization as org_repo
from app.repositories import project as project_repo
from app.work import contracts as C
from app.work import work_intake

logger = logging.getLogger(__name__)

ACTION_ROUTED = "routed"
ACTION_BOUND = "bound"
ACTION_DECLINED = "declined"

#: 承接方口径（订单被哪家公司接下的经济事实，M1 冻结）
ASSIGNEE_KIND_COMPANY = "company"


class WorkOrderBridgeError(C.WorkContractError):
    """绑定契约被违反（引用不合法 / 越权 / 状态不允许）。`http_status` 由 API 翻。"""

    def __init__(self, reason: str, http_status: int = 422) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status


# ---------------------------------------------------------------------------
# § 读模型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BindingEdge:
    """绑定边上的一条事实/决定。"""

    link_id: int
    action: str
    project_id: int | None
    actor_employee_id: int | None
    reason: str
    metadata: dict
    created_at: str

    def as_dict(self) -> dict:
        return {
            "link_id": self.link_id,
            "action": self.action,
            "project_id": self.project_id,
            "actor_employee_id": self.actor_employee_id,
            "reason": self.reason,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


def links_for_order(db: Session, work_order_id: int) -> list[WorkOrderProjectLink]:
    return list(
        db.scalars(
            select(WorkOrderProjectLink)
            .where(WorkOrderProjectLink.work_order_id == int(work_order_id))
            .order_by(WorkOrderProjectLink.id)
        )
    )


def _edge(row: WorkOrderProjectLink) -> BindingEdge:
    return BindingEdge(
        link_id=int(row.id),
        action=row.action,
        project_id=int(row.project_id) if row.project_id else None,
        actor_employee_id=int(row.actor_employee_id) if row.actor_employee_id else None,
        reason=row.reason or "",
        metadata=dict(row.metadata_json or {}),
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


def accepted_company_id(order: WorkOrder) -> int | None:
    """承接公司（`assignee_actor_kind='company'` 时的 ref）；否则 None。"""
    if str(order.assignee_actor_kind or "") != ASSIGNEE_KIND_COMPANY:
        return None
    return int(order.assignee_actor_ref or 0) or None


def binding_view(db: Session, order: WorkOrder) -> dict:
    """订单的执行绑定视图：**事实 + 决定**（J1 的两条路径都能看见）。"""
    rows = links_for_order(db, int(order.id))
    edges = [_edge(row) for row in rows]
    company_id = accepted_company_id(order)
    project = project_repo.get_project(db, int(order.project_id)) if order.project_id else None
    return {
        "work_order_id": int(order.id),
        "status": str(order.status),
        "accepted_company_id": company_id,
        "project_id": int(order.project_id) if order.project_id else None,
        "project_name": project.name if project else None,
        "routed": next((edge.as_dict() for edge in edges if edge.action == ACTION_ROUTED), None),
        "bound": next((edge.as_dict() for edge in edges if edge.action == ACTION_BOUND), None),
        "declined": next(
            (edge.as_dict() for edge in edges if edge.action == ACTION_DECLINED), None
        ),
        "edges": [edge.as_dict() for edge in edges],
        "deliverable_facts": _deliverable_facts(db, order, project),
        "rules": list(C.WORK_ORDER_BINDING_RULES),
    }


def _deliverable_facts(db: Session, order: WorkOrder, project) -> dict:
    """订单要求 vs 项目声明的交付物 —— **事实**（差异是判断，系统不下结论）。"""
    order_deliverables = sorted((order.deliverables_json or {}).keys())
    project_deliverables = (
        sorted(str(item) for item in (project.deliverables or []) if item) if project else []
    )
    return {
        "order_deliverables": order_deliverables,
        "project_deliverables": project_deliverables,
        "declared_not_matched": sorted(set(order_deliverables) - set(project_deliverables)),
        "note": "交付意图是否吻合由管理层判断；系统只报事实差异",
    }


# ---------------------------------------------------------------------------
# § 系统投递（WO2/WO5：只说"该谁看"，不做任何决定）
# ---------------------------------------------------------------------------


def route_accepted_order(db: Session, order: WorkOrder, *, commit: bool = True) -> dict | None:
    """把已承接的订单投递给公司的 Work Intake 责任人（**幂等**）。

    - 只对 `ACCEPTED` 之后（含 IN_PROGRESS/SUBMITTED…）且承接方是公司的订单生效；
    - 已经 `routed`/`bound`/`declined` 过 ⇒ 什么都不做（重放安全）；
    - **不创建 Project**、不发任何"决策"事件：只写一条 `routed` 事实。
    """
    company_id = accepted_company_id(order)
    if company_id is None:
        return None
    if str(order.status) == WorkOrderStatus.open.value:
        return None
    existing = links_for_order(db, int(order.id))
    if any(row.action in {ACTION_ROUTED, ACTION_BOUND, ACTION_DECLINED} for row in existing):
        return None

    company = org_repo.get_company(db, company_id)
    if company is None:  # pragma: no cover - 防御
        return None
    resolution = work_intake.resolve_work_intake(db, company)
    owner_id = int(resolution.employee_id) if resolution.employee_id else None
    row = WorkOrderProjectLink(
        work_order_id=int(order.id),
        action=ACTION_ROUTED,
        project_id=None,
        actor_employee_id=None,  # 系统投递：没有"决策人"
        reason="work_intake_routed" if owner_id else "waiting_for_management",
        metadata_json={
            "position_code": resolution.configured_position_code,
            "candidate_employee_ids": list(resolution.candidate_employee_ids or ()),
            "owner_user_id": resolution.owner_user_id,
            "routed_employee_id": owner_id,
            "needs_owner_attention": bool(resolution.needs_owner_attention),
        },
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
    bus.publish(
        "work_order.intake_routed",
        {
            "work_order_id": int(order.id),
            "company_id": company_id,
            "employee_id": owner_id,
            "position_code": resolution.configured_position_code,
            "intake_status": str(resolution.status),
        },
        company_id=company_id,
        actor_employee_id=owner_id,
    )
    return _edge(row).as_dict()


# ---------------------------------------------------------------------------
# § 管理决定：绑定 / 拒绝（WO2/WO3）
# ---------------------------------------------------------------------------


def bind_project(
    db: Session,
    order: WorkOrder,
    *,
    project_id: int,
    actor_employee_id: int,
    reason: str = "",
    commit: bool = True,
) -> WorkOrder:
    """把某个 Project 绑到这份订单上（**管理决定**，WO2/WO3）。

    硬约束（不是判断）：
    - 订单承接方必须是**公司**，且公司要匹配（否则 404：不是你的订单）；
    - 订单必须已经 ACCEPTED（未承接的商业需求没有执行载体可言）；
    - `project_id` 必须存在（422）**且属于同一家公司**（跨公司 404，J3）；
    - 已经 `declined` 的订单不能再绑（先撤销那条拒绝，或换一份订单）。
    """
    actor = _require_actor(db, actor_employee_id, order, http_status=422)
    if str(order.status) == WorkOrderStatus.open.value:
        raise WorkOrderBridgeError("work_order_not_accepted", http_status=409)
    existing = links_for_order(db, int(order.id))
    if any(row.action == ACTION_DECLINED for row in existing):
        raise WorkOrderBridgeError("work_order_binding_declined", http_status=409)

    company_id = accepted_company_id(order)
    assert company_id is not None  # 由 work_order_not_accepted 保证
    if int(actor.company_id or 0) != int(company_id):
        raise WorkOrderBridgeError("not_your_order", http_status=404)

    project = project_repo.get_project(db, int(project_id))
    if project is None:
        raise WorkOrderBridgeError("project_not_found", http_status=422)
    if int(project.company_id) != int(company_id):
        # 跨公司：**不存在的口径**（J3）
        raise WorkOrderBridgeError("project_not_found", http_status=404)

    order.project_id = int(project.id)
    row = WorkOrderProjectLink(
        work_order_id=int(order.id),
        action=ACTION_BOUND,
        project_id=int(project.id),
        actor_employee_id=int(actor.id),
        reason=reason,
        metadata_json={
            "deliverable_facts": _deliverable_facts(db, order, project),
            "project_status": str(project.status),
            "project_work_mode": getattr(project, "work_mode", None),
        },
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
    bus.publish(
        "work_order.project_bound",
        {
            "work_order_id": int(order.id),
            "project_id": int(project.id),
            "company_id": company_id,
        },
        company_id=company_id,
        actor_employee_id=int(actor.id),
        project_id=int(project.id),
    )
    return order


def decline_binding(
    db: Session,
    order: WorkOrder,
    *,
    actor_employee_id: int,
    reason: str,
    commit: bool = True,
) -> dict:
    """明确**不接**这份订单（J1 的另一条路径；理由必填）。"""
    if not str(reason or "").strip():
        raise WorkOrderBridgeError("declining requires a reason", http_status=422)
    actor = _require_actor(db, actor_employee_id, order, http_status=422)
    existing = links_for_order(db, int(order.id))
    if any(row.action == ACTION_BOUND for row in existing):
        raise WorkOrderBridgeError("work_order_already_bound", http_status=409)
    if any(row.action == ACTION_DECLINED for row in existing):
        raise WorkOrderBridgeError("work_order_already_declined", http_status=409)

    company_id = accepted_company_id(order)
    row = WorkOrderProjectLink(
        work_order_id=int(order.id),
        action=ACTION_DECLINED,
        project_id=None,
        actor_employee_id=int(actor.id),
        reason=str(reason).strip(),
        metadata_json={"declined_by": int(actor.id)},
    )
    db.add(row)
    db.flush()
    if commit:
        db.commit()
    bus.publish(
        "work_order.binding_declined",
        {"work_order_id": int(order.id), "company_id": company_id, "reason": str(reason).strip()},
        company_id=company_id,
        actor_employee_id=int(actor.id),
    )
    return _edge(row).as_dict()


def _require_actor(
    db: Session, actor_employee_id: int | None, order: WorkOrder, *, http_status: int
) -> Employee:
    if actor_employee_id is None:
        raise WorkOrderBridgeError("actor_employee_id is required", http_status=http_status)
    employee = db.get(Employee, int(actor_employee_id))
    if employee is None:
        raise WorkOrderBridgeError("actor not found", http_status=http_status)
    return employee


__all__ = [
    "ACTION_BOUND",
    "ACTION_DECLINED",
    "ACTION_ROUTED",
    "ASSIGNEE_KIND_COMPANY",
    "BindingEdge",
    "WorkOrderBridgeError",
    "accepted_company_id",
    "bind_project",
    "binding_view",
    "decline_binding",
    "links_for_order",
    "route_accepted_order",
]

"""职位事件 → `role.context_available` / `role.context_withdrawn` 通知（M2.2）。

这个消费者只做**一件事**：任职发生变化时，把"这个人的履职上下文变了"这件事
连同**事实**广播出去（职位、生效授权条数、资源条数、授权摘要）。

它**不做**的事（设计 §8 / W39）：

* 不发"请去学习 X"的系统指令 —— 学什么、缺什么由 Agent 自己判断（Adaptive Onboarding）；
* 不给任何人打权限、不改任何行 —— 它是通知，不是收敛器
  （权限开通另有 `workforce/access.py` 的 Desired State 消费者）；
* 不做任何管理判断。

与 `workforce/access.py` 同款纪律：处理在**分配事务之外**，幂等（只读 + 发通知），
按 employee 分区保序。
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.events.bus import bus
from app.models.organization import Employee
from app.work import authority as authority_service
from app.work import role_context as role_context_service

logger = logging.getLogger("eidolon.work.role")

POSITION_EVENTS = ("employee.position_assigned", "employee.position_released")


def _partition_key(message: dict):
    return (message.get("data") or {}).get("id") or (message.get("data") or {}).get("employee_id")


def _facts(db: Session, employee_id: int) -> dict | None:
    """履职上下文的**事实摘要**（不含任何"该怎么工作"的建议）。"""
    employee = db.get(Employee, int(employee_id))
    if employee is None:
        return None
    actor = authority_service.resolve_actor_authority(db, int(employee_id))
    resources = role_context_service.resolve_role_resources(db, employee)
    return {
        "employee_id": int(employee.id),
        "person_id": int(employee.person_id) if employee.person_id is not None else None,
        "position_definition_id": actor.position_definition_id,
        "position_code": actor.position_code,
        "slot_id": actor.slot_id,
        "department_id": actor.department_id,
        "authority_grant_count": len(actor.grants),
        "authority_grants_hash": actor.grants_hash,
        "role_resource_count": len(resources),
        "role_resource_missing": [
            item.ref for item in resources if item.resolution.value != "resolved"
        ],
    }


def handle(message: dict) -> None:
    """把一条职位事件翻译成履职上下文通知（纯读 + 广播）。"""
    data = message.get("data") or {}
    employee_id = data.get("id") or data.get("employee_id")
    if employee_id is None:
        return
    event_type = message.get("type")
    with SessionLocal() as db:
        facts = _facts(db, int(employee_id))
    if facts is None:
        return
    company_id = message.get("company_id")
    if event_type == "employee.position_assigned":
        bus.publish(
            "role.context_available",
            {**facts, "via": str(data.get("reason") or "")},
            company_id=company_id,
            actor_employee_id=int(employee_id),
        )
    else:
        bus.publish(
            "role.context_withdrawn",
            {
                "employee_id": facts["employee_id"],
                "person_id": facts["person_id"],
                "position_definition_id": None,
                "position_code": None,
                "authority_grant_count": 0,
                "role_resource_count": 0,
                "via": str(data.get("reason") or ""),
            },
            company_id=company_id,
            actor_employee_id=int(employee_id),
        )


def register(event_engine) -> None:
    """挂到事件引擎（按 employee 分区保序；幂等，重放安全）。"""
    event_engine.register("role-context", POSITION_EVENTS, handle, key_of=_partition_key)


def enabled() -> bool:
    return bool(settings.role_context_events)

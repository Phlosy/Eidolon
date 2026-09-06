"""职位兼容层 —— **全项目唯一允许读 `employees.role` 的模块**（ADR-5 / docs §6）。

它存在的目的恰恰是让旧字段尽快死掉：

- 旧前端 / 旧 API 还在读 `role` 字符串，所以响应继续给 —— 但**值来自这里**，
  而这里的真值是 `生效 PRIMARY 任职 → PositionSlot → PositionDefinition`。
- `employees.role` 从此只是一个**写给人看的镜像**（P6 起由本模块反向刷新），
  不再有任何业务判断读它。P4c 的架构守卫会把"本模块之外读 `employee.role`"变成红测试。

一句话：**老代码还能跑，但任何新代码都不必再碰旧字段。**
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.models.enums import EmployeeRole
from app.models.organization import Employee
from app.repositories import position as position_repo
from app.schemas.position import CurrentPositionOut

# 没有任何主职、也没有可信镜像时的兜底值。
# 为什么兜 `engineer` 而不是抛错：旧前端的 `role` 是非空枚举，返回 null 会让它
# 在渲染处炸掉；而这不是"猜一个人"，只是给一个能跑起来的最小可读值。
_FALLBACK_ROLE = EmployeeRole.engineer.value


def derived_current_position(db: Session, employee: Employee) -> CurrentPositionOut | None:
    """当前职位视图；没有主职就返回 `None`。

    `None` 是合法答案（= `AVAILABLE`），**不**回落到 `employees.role` 假装有一个职位 ——
    那正是本次重构要消灭的第二个真相。
    """
    current = position_repo.employee_current_position(db, int(employee.id))
    if current is None:
        return None
    return CurrentPositionOut(
        definition_id=current.definition_id,
        code=current.code,
        name=current.name,
        level=current.level,
        job_family=current.job_family,
        legacy_role=current.legacy_role,
        department_id=current.department_id,
        department_name=current.department_name,
        slot_id=current.slot_id,
        slot_code=current.slot_code,
        since=current.since,
        assignment_type=current.assignment_type,
        position_is_custom=current.is_custom,
    )


def legacy_role_of(db: Session, employee: Employee) -> str:
    """旧 `role` 口径的取值顺序（每一步都写在返回值里，不留隐式魔法）：

    1. 生效主职的职位定义带 `legacy_role` —— 真值，占绝大多数；
    2. 自定义职位（无 legacy 对应）⇒ 如实返回 `engineer` 兜底，并由
       `derived_current_position().position_is_custom = True` 说明"这不是真的 role"；
    3. 完全没有主职（`AVAILABLE`）⇒ 读 `employees.role` **镜像**，只为旧前端不破；
    4. 镜像也不是合法枚举值 ⇒ 兜 `engineer`。
    """
    current = position_repo.employee_current_position(db, int(employee.id))
    if current is not None:
        # 有主职时以职位定义为准；自定义职位没有 legacy 对应，不谎报成某个存在的 role。
        return current.legacy_role or _FALLBACK_ROLE
    mirror = (employee.role or "").strip()
    if mirror in {member.value for member in EmployeeRole}:
        return mirror
    return _FALLBACK_ROLE


def workforce_status_of(db: Session, employee: Employee) -> str:
    """把两个轴合成一个给旧调用点用的字符串（内部仍走唯一 resolver）。"""
    from app.workforce.status import WorkforceStatusResolver

    return WorkforceStatusResolver(db).resolve(employee).value


def enrich_employee(db: Session, employee: Employee, payload: dict) -> dict:
    """给员工响应补 `current_position` / `workforce_status`，并让 `role` 成为派生镜像。

    放在这里而不是各个 API 里，是为了保证"同一个字段在哪个接口都是同一个算法"。
    """
    position = derived_current_position(db, employee)
    payload["current_position"] = position.model_dump() if position is not None else None
    payload["workforce_status"] = workforce_status_of(db, employee)
    payload["role"] = legacy_role_of(db, employee)
    return payload


def scoped_company_id(db: Session, employee: Employee) -> int | None:
    """兼容层也需要知道公司边界（守卫测试要能构造跨公司场景）。"""
    identity = get_request_identity()
    if identity is not None:
        return identity.company_id
    return employee.company_id

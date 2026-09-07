"""职位兼容层 —— **全项目唯一允许读 `employees.role` 的模块**（ADR-5 / docs §6）。

它存在的目的恰恰是让旧字段尽快死掉：

- 旧前端 / 旧 API 还在读 `role` 字符串，所以响应继续给 —— 但**值来自这里**，
  而这里的真值是 `生效 PRIMARY 任职 → PositionSlot → PositionDefinition`。
- `employees.role` 从此只是一个**写给人看的镜像**（P6 起由本模块反向刷新），
  不再有任何业务判断读它。P4c 的架构守卫会把"本模块之外读 `employee.role`"变成红测试。

一句话：**老代码还能跑，但任何新代码都不必再碰旧字段。**
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.core.request_context import get_request_identity
from app.models.enums import EmployeeRole
from app.models.organization import Employee
from app.repositories import organization as org_repo
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


def role_mirror(db: Session, employees: Sequence[Employee]) -> dict[int, str]:
    """一批人的旧 `role` 口径取值（**批量**：一页名册只查一次职位）。

    取值顺序（每一步都写在返回值里，不留隐式魔法）：

    1. 生效主职的职位定义带 `legacy_role` —— 真值，占绝大多数；自定义职位没有 legacy
       对应，如实兜 `engineer`，并由 `derived_current_position().position_is_custom`
       说明"这不是真的 role"；
    2. 完全没有主职（`AVAILABLE`）⇒ 读 `employees.role` **镜像**，只为旧前端不破；
    3. 镜像也不是合法枚举值 ⇒ 兜 `engineer`。

    这是全仓唯一允许读 `employee.role` 的地方（P4c 的架构守卫按此放行）。
    """
    ids = [int(e.id) for e in employees]
    positions = position_repo.current_positions_by_employee(db, ids)
    valid = {member.value for member in EmployeeRole}
    out: dict[int, str] = {}
    for employee in employees:
        current = positions.get(int(employee.id))
        if current is not None:
            out[int(employee.id)] = current.legacy_role or _FALLBACK_ROLE
            continue
        mirror = (employee.role or "").strip()
        out[int(employee.id)] = mirror if mirror in valid else _FALLBACK_ROLE
    return out


def legacy_role_of(db: Session, employee: Employee) -> str:
    """单个员工的旧 `role` 口径 —— 走 `role_mirror`，避免两份实现漂移。"""
    return role_mirror(db, [employee])[int(employee.id)]


def employee_by_legacy_role(db: Session, company_id: int, role: str) -> Employee | None:
    """按旧 `role` 口径找人：**先问谁真的占着那个编制**，问不到才回退旧列。

    `projects` / `project_delivery` / `tutorial` 里"派给 QA""找 CEO 审批"这类分支原来直接
    `WHERE employees.role = 'qa_engineer'`，于是"没任职但 role 写着 qa_engineer 的人"也会被派活
    （名册上他明明是 `AVAILABLE`）。这里把优先级倒回来：

    1. 生效 PRIMARY 占着"声明了该 role 的职位定义"的人 —— 组织事实；
    2. 没有这种人（v0.4 老数据、只招不派的公司都属此类）⇒ 回退旧列镜像，
       行为与改之前一致，不制造新的"找不到人"。

    回退分支保留是因为**它还必须能用**：删掉它等于在 P6 之前让老公司派不出活。
    """
    holder_id = position_repo.employee_id_holding_legacy_role(db, company_id, role)
    if holder_id is not None:
        holder = db.get(Employee, holder_id)
        if holder is not None:
            return holder
    return org_repo.get_employee_by_role(db, company_id, role)


def workforce_status_of(db: Session, employee: Employee) -> str:
    """把两个轴合成一个给旧调用点用的字符串（内部仍走唯一 resolver）。"""
    from app.workforce.status import WorkforceStatusResolver

    return WorkforceStatusResolver(db).resolve(employee).value


def enrich_employee(db: Session, employee: Employee, payload: dict) -> dict:
    """给员工响应补 `current_position` / `workforce_status`，并让 `role` 成为派生镜像。

    放在这里而不是各个 API 里，是为了保证"同一个字段在哪个接口都是同一个算法"。
    """
    from app.workforce.status import WorkforceStatusResolver

    # 一次派生，三个字段同源：`workforce_status` / `has_primary_assignment` /
    # `occupies_establishment` 都取自同一个 WorkforceView。分开各算一遍的话，
    # 同一份响应里可能出现"状态是 ASSIGNED 但 occupies=False"这种自相矛盾的行。
    view = WorkforceStatusResolver(db).view(employee)
    position = derived_current_position(db, employee)
    payload["current_position"] = position.model_dump() if position is not None else None
    payload["workforce_status"] = view.workforce_status.value
    payload["has_primary_assignment"] = view.has_primary_assignment
    payload["occupies_establishment"] = view.occupies_establishment
    payload["role"] = legacy_role_of(db, employee)
    return payload


def scoped_company_id(db: Session, employee: Employee) -> int | None:
    """兼容层也需要知道公司边界（守卫测试要能构造跨公司场景）。"""
    identity = get_request_identity()
    if identity is not None:
        return identity.company_id
    return employee.company_id

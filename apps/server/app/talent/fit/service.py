"""Position Fit Service —— 计算入口（P8 §19）与公司隔离。

read model：纯计算；不写 EmployeeCompetency / PositionRequirement / Assignment /
Assessment / Brain。Company 隔离：员工与职位必须同一公司（系统模板 company=None
允许作为上下文，但实际匹配优先公司画像）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.organization import Employee
from app.models.position import PositionDefinition
from app.talent.fit import engine
from app.talent.fit.engine import calculate
from app.talent.fit.models import PositionFitResult


class FitDomainError(ValueError):
    """域名/边界错误（API 层转 404/422）。"""


def calculate_fit(
    db: Session,
    *,
    employee_id: int,
    position_definition_id: int,
    profile_version_id: int | None = None,
) -> PositionFitResult:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise FitDomainError("employee not found")
    position = db.get(PositionDefinition, position_definition_id)
    if position is None:
        raise FitDomainError("position not found")
    if position.company_id is not None and position.company_id != employee.company_id:
        raise FitDomainError("employee and position belong to different companies")
    return calculate(
        db,
        employee_id=employee_id,
        position=position,
        profile_version_id=profile_version_id,
    )


def calculate_person_fit(
    db: Session,
    *,
    person_id: int,
    position_definition_id: int,
    company_id: int | None,
    profile_version_id: int | None = None,
) -> PositionFitResult:
    """Person × Position 的匹配（T2.5）：市场候选人没有 employee 行。

    与员工入口**共用同一核心**（`engine.calculate_for_person` → `_calculate`），
    同一 person 两条路径结果逐字段一致（含 inputs_hash，见 tests/test_person_fit.py）。

    公司边界：职位属本公司（或全局模板 company_id IS NULL），否则 404 语义
    （`position not found`，不泄露存在性 —— 与市场读面一致）。
    """
    from app.repositories import persons as person_repo

    if person_repo.get_person(db, person_id) is None:
        raise FitDomainError("person not found")
    position = db.get(PositionDefinition, position_definition_id)
    if position is None:
        raise FitDomainError("position not found")
    if position.company_id is not None and position.company_id != company_id:
        raise FitDomainError("position not found")
    return engine.calculate_for_person(
        db,
        person_id=person_id,
        position=position,
        profile_version_id=profile_version_id,
    )


def current_position_fit(db: Session, *, employee_id: int) -> PositionFitResult | None:
    """员工当前任职职位的 Fit（无任职则 None）。"""
    from app.repositories import position as position_repo

    current = position_repo.employee_current_position(db, employee_id)
    if current is None:
        return None
    return calculate_fit(
        db,
        employee_id=employee_id,
        position_definition_id=current.definition_id,
    )

"""Position Fit Service —— 计算入口（P8 §19）与公司隔离。

read model：纯计算；不写 EmployeeCompetency / PositionRequirement / Assignment /
Assessment / Brain。Company 隔离：员工与职位必须同一公司（系统模板 company=None
允许作为上下文，但实际匹配优先公司画像）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.organization import Employee
from app.models.position import PositionDefinition
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

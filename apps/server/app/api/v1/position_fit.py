"""Position Fit API（P8 §34）—— Employee × Position 一对一分析（只读）。

不做全公司 ranking / recommended-candidates（P9）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.schemas.position_fit import PositionFitOut
from app.talent.fit import serializer as fit_serializer
from app.talent.fit import service as fit_service

router = APIRouter(tags=["position-fit"])


def _domain_error(exc: fit_service.FitDomainError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


@router.get(
    "/employees/{employee_id}/position-fit/{position_definition_id}",
    response_model=PositionFitOut,
)
def employee_position_fit(
    employee_id: int,
    position_definition_id: int,
    profile_version_id: int | None = Query(None, description="显式画像版本（缺省 ACTIVE）"),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    try:
        result = fit_service.calculate_fit(
            db,
            employee_id=employee_id,
            position_definition_id=position_definition_id,
            profile_version_id=profile_version_id,
        )
    except fit_service.FitDomainError as exc:
        raise _domain_error(exc) from exc
    return fit_serializer.serialize(result)


@router.get(
    "/employees/{employee_id}/position-fit/current",
    response_model=PositionFitOut | None,
)
def employee_current_position_fit(
    employee_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict | None:
    """当前任职职位的 Fit（无任职 ⇒ null）。"""
    from app.api.v1.assessment import _employee_or_404 as employee_exists

    employee_exists(db, employee_id, company_id)
    try:
        result = fit_service.current_position_fit(db, employee_id=employee_id)
    except fit_service.FitDomainError as exc:
        raise _domain_error(exc) from exc
    if result is None:
        return None
    return fit_serializer.serialize(result)

"""Competency read API（P5）—— 只读；没有 PATCH score 入口。

端点（docs/competency-system.md §8 的 P5 子集）：

    GET /competency-domains?kind=             目录（全局内置 + 本公司自定义）
    GET /competencies?domain_id=&kind=        能力定义（公司 scope）
    GET /employees/{id}/competencies          通用（10 维全量，unrated 如实 null）
                                             + 专业（动态目录，仅已评估）
    GET /employees/{id}/traits                8 维人格（0..1 + 展示值 + affects_execution）
    GET /employees/{id}/competency-evidence   证据（可追溯；带能力 code/name）

员工能力分只能由 Assessment（聚合服务）更新 —— 这里不提供写端点。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.models.organization import Employee
from app.repositories import competency as competency_repo
from app.schemas.competency import (
    CompetencyDefinitionOut,
    CompetencyDomainOut,
    CompetencyEvidenceOut,
    EmployeeCapabilitiesOut,
)
from app.services import competency as competency_service
from app.services import traits as traits_service

router = APIRouter(tags=["competency"])


def _employee_or_404(db: Session, employee_id: int, company_id: int | None) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    if company_id is not None and employee.company_id != company_id:
        raise HTTPException(status_code=404, detail="employee not found")
    return employee


@router.get("/competency-domains", response_model=list[CompetencyDomainOut])
def list_competency_domains(
    kind: str | None = Query(None, description="general | professional"),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    return competency_repo.list_domains(db, company_id=company_id, kind=kind)


@router.get("/competencies", response_model=list[CompetencyDefinitionOut])
def list_competencies(
    domain_id: int | None = None,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    """能力定义。公司 scope 读面 = 全局内置 + 本公司自定义。"""
    domain_ids = None
    if domain_id is not None:
        domains = competency_repo.list_domains(db, company_id=company_id)
        if domain_id not in {domain.id for domain in domains}:
            raise HTTPException(status_code=404, detail="domain not found")
        domain_ids = [domain_id]
    return competency_repo.list_definitions(db, domain_ids=domain_ids)


@router.get("/employees/{employee_id}/competencies", response_model=EmployeeCapabilitiesOut)
def employee_capabilities(
    employee_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    _employee_or_404(db, employee_id, company_id)
    return competency_service.employee_capabilities_out(db, employee_id)


@router.get("/employees/{employee_id}/traits")
def employee_traits(
    employee_id: int,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    _employee_or_404(db, employee_id, company_id)
    return traits_service.employee_traits_out(db, employee_id)


@router.get(
    "/employees/{employee_id}/competency-evidence",
    response_model=list[CompetencyEvidenceOut],
)
def employee_competency_evidence(
    employee_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    """该员工的证据（按时间倒序）。每条都能反查到源对象（source_kind/source_ref）。"""
    _employee_or_404(db, employee_id, company_id)
    rows = competency_repo.list_evidence(db, employee_id, limit=limit)
    definitions = competency_repo.definitions_by_id(
        db, [row.competency_definition_id for row in rows]
    )
    out = []
    for row in rows:
        definition = definitions.get(row.competency_definition_id)
        out.append(
            {
                "id": row.id,
                "employee_id": row.employee_id,
                "competency_definition_id": row.competency_definition_id,
                "competency_code": definition.code if definition else "",
                "competency_name": definition.name if definition else "",
                "source_kind": row.source_kind,
                "source_id": row.source_id,
                "source_ref": row.source_ref,
                "assessment_run_id": row.assessment_run_id,
                "signal": row.signal,
                "quality": row.quality,
                "occurred_at": row.occurred_at,
            }
        )
    return out

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

from datetime import datetime

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.models.competency import (
    CompetencyDefinition,
    CompetencyDomain,
    CompetencyEvidence,
)
from app.models.organization import Employee
from app.repositories import competency as competency_repo
from app.repositories import persons as person_repo
from app.schemas.competency import (
    CompetencyDefinitionOut,
    CompetencyDomainOut,
    CompetencyEvidenceOut,
    EmployeeCapabilitiesOut,
)
from app.services import competency as competency_service
from app.services import traits as traits_service

router = APIRouter(tags=["competency"])


def _resolve_definition(db: Session, competency: str) -> CompetencyDefinition:
    """按 code（全局目录）或数字 id 解析能力定义。"""
    if competency.isdigit():
        definition = db.get(CompetencyDefinition, int(competency))
        if definition is None:
            raise HTTPException(status_code=404, detail="competency not found")
        return definition
    definition = db.scalar(
        select(CompetencyDefinition)
        .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
        .where(
            CompetencyDefinition.code == competency,
            CompetencyDomain.company_id.is_(None),
        )
    )
    if definition is None:
        raise HTTPException(status_code=404, detail="competency not found")
    return definition


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
    competency: str | None = Query(None, description="competency code（或数字 id），可选"),
    source_type: str | None = Query(None, description="EvidenceSourceKind，如 test/review"),
    assessment_run_id: int | None = Query(None, description="只查被某次 run 引用的证据"),
    occurred_from: datetime | None = Query(None),
    occurred_to: datetime | None = Query(None),
    limit: int = Query(default=100, ge=1, le=500),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    """该员工的证据（按时间倒序）。每条都能反查到源对象（source_kind/source_ref）。

    过滤参数可选：competency / source_type / assessment_run_id / 时间区间。
    没有任意 POST evidence：人工反馈必须走明确业务入口（如技能评价）。
    """
    _employee_or_404(db, employee_id, company_id)
    query = sa.select(CompetencyEvidence).where(
        person_repo.read_criterion(
            db, employee_id, CompetencyEvidence.person_id, CompetencyEvidence.employee_id
        )
    )
    if source_type:
        query = query.where(CompetencyEvidence.source_kind == source_type)
    if assessment_run_id is not None:
        query = query.where(CompetencyEvidence.assessment_run_id == assessment_run_id)
    if occurred_from is not None:
        query = query.where(CompetencyEvidence.occurred_at >= occurred_from)
    if occurred_to is not None:
        query = query.where(CompetencyEvidence.occurred_at <= occurred_to)
    if competency:
        definition = _resolve_definition(db, competency)
        query = query.where(CompetencyEvidence.competency_definition_id == definition.id)
    rows = list(
        db.scalars(
            query.order_by(
                CompetencyEvidence.occurred_at.desc(), CompetencyEvidence.id.desc()
            ).limit(limit)
        )
    )
    # T2.1：mapping 与人员证据读面共用一份（competency_service.evidence_payload）
    return [
        {"employee_id": row.employee_id, **item}
        for row, item in zip(rows, competency_service.evidence_payload(db, rows), strict=True)
    ]

"""Competency read repository (P5). 只读；写面只有聚合服务（app/services/competency.py）。

读取一律显式查询（不建 relationship，避免 N+1 与"随手取第一个"）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.competency import (
    CompetencyDefinition,
    CompetencyDomain,
    CompetencyEvidence,
    EmployeeCompetency,
)
from app.models.enums import CompetencyKind


def list_domains(
    db: Session,
    company_id: int | None = None,
    kind: str | None = None,
) -> list[CompetencyDomain]:
    """能力域：全局内置行（company_id NULL）+ 本公司自定义行；可过滤 kind。"""
    query = select(CompetencyDomain).order_by(CompetencyDomain.order_index, CompetencyDomain.id)
    if company_id is not None:
        query = query.where(
            (CompetencyDomain.company_id.is_(None)) | (CompetencyDomain.company_id == company_id)
        )
    if kind is not None:
        query = query.where(CompetencyDomain.kind == kind)
    return list(db.scalars(query))


def list_definitions(
    db: Session, domain_ids: list[int] | None = None, definition_ids: list[int] | None = None
) -> list[CompetencyDefinition]:
    query = select(CompetencyDefinition).order_by(
        CompetencyDefinition.domain_id, CompetencyDefinition.order_index, CompetencyDefinition.id
    )
    if domain_ids:
        query = query.where(CompetencyDefinition.domain_id.in_(domain_ids))
    if definition_ids:
        query = query.where(CompetencyDefinition.id.in_(definition_ids))
    return list(db.scalars(query))


def definitions_by_id(db: Session, definition_ids: list[int]) -> dict[int, CompetencyDefinition]:
    return {
        definition.id: definition
        for definition in list_definitions(db, definition_ids=definition_ids)
    }


def employee_competency_rows(
    db: Session, employee_id: int, definition_ids: list[int] | None = None
) -> list[EmployeeCompetency]:
    query = select(EmployeeCompetency).where(EmployeeCompetency.employee_id == employee_id)
    if definition_ids:
        query = query.where(EmployeeCompetency.competency_definition_id.in_(definition_ids))
    return list(db.scalars(query))


def list_evidence(
    db: Session,
    employee_id: int,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[CompetencyEvidence]:
    return list(
        db.scalars(
            select(CompetencyEvidence)
            .where(CompetencyEvidence.employee_id == employee_id)
            .order_by(CompetencyEvidence.occurred_at.desc(), CompetencyEvidence.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )


def general_definitions(db: Session) -> list[CompetencyDefinition]:
    """全部通用能力定义（company NULL 的 general 域）。"""
    domain = db.scalar(
        select(CompetencyDomain).where(
            CompetencyDomain.company_id.is_(None),
            CompetencyDomain.kind == CompetencyKind.general.value,
        )
    )
    if domain is None:
        return []
    return list_definitions(db, domain_ids=[domain.id])

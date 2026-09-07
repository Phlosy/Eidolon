"""Expectations —— 工作项/职位模板 期望验证哪些能力（docs/evidence-pipeline.md §七）。

Position 只提供 Expectation Template；真实工作完成才产生 Evidence。
能力 code → 内置职位模板期望在此集中声明（seed，幂等）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assessment import CompetencyExpectation
from app.models.competency import CompetencyDefinition, CompetencyDomain
from app.models.enums import ExpectationRole
from app.repositories import position as position_repo

#: 内置职位模板（position_definition.code）→ [(competency_code, role)]。
BUILTIN_POSITION_EXPECTATIONS: dict[str, list[tuple[str, str]]] = {
    "engineer": [
        ("execution", ExpectationRole.primary.value),
        ("backend_engineering", ExpectationRole.primary.value),
        ("code_quality", ExpectationRole.supporting.value),
        ("quality_reliability", ExpectationRole.supporting.value),
        ("testing", ExpectationRole.supporting.value),
    ],
    "qa_engineer": [
        ("testing", ExpectationRole.primary.value),
        ("quality_reliability", ExpectationRole.primary.value),
        ("execution", ExpectationRole.supporting.value),
        ("communication", ExpectationRole.supporting.value),
    ],
    "researcher": [
        ("analysis_problem_solving", ExpectationRole.primary.value),
        ("information_retrieval", ExpectationRole.primary.value),
        ("evidence_synthesis", ExpectationRole.primary.value),
        ("technical_writing", ExpectationRole.supporting.value),
        ("communication", ExpectationRole.supporting.value),
    ],
    "product_manager": [
        ("planning_organization", ExpectationRole.primary.value),
        ("communication", ExpectationRole.primary.value),
        ("requirements_analysis", ExpectationRole.primary.value),
        ("decision_making", ExpectationRole.supporting.value),
    ],
    "ceo": [
        ("management_leadership", ExpectationRole.primary.value),
        ("decision_making", ExpectationRole.primary.value),
        ("planning_organization", ExpectationRole.primary.value),
        ("communication", ExpectationRole.supporting.value),
    ],
}


def expectations_for_scope(
    db: Session, scope_kind: str, scope_id: int
) -> list[CompetencyExpectation]:
    return list(
        db.scalars(
            select(CompetencyExpectation).where(
                CompetencyExpectation.scope_kind == scope_kind,
                CompetencyExpectation.scope_id == scope_id,
            )
        )
    )


def _definition_by_code_global(db: Session, code: str) -> CompetencyDefinition | None:
    return db.scalar(
        select(CompetencyDefinition)
        .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
        .where(
            CompetencyDefinition.code == code,
            CompetencyDomain.company_id.is_(None),
        )
    )


def _position_template_by_code(db: Session, code: str) -> int | None:
    from app.models.position import PositionDefinition

    return db.scalar(
        select(PositionDefinition.id).where(
            PositionDefinition.company_id.is_(None),
            PositionDefinition.code == code,
        )
    )


def position_expectations(db: Session, employee_id: int) -> list[CompetencyExpectation]:
    """员工当前任职定义（或同 code 的系统模板）上的能力期望。

    没有任职的人返回空列表 —— 他们没有被期望证明任何能力（能力只能被证明）。
    """
    current = position_repo.employee_current_position(db, employee_id)
    if current is None:
        return []
    rows = expectations_for_scope(db, "position_definition", current.definition_id)
    if rows:
        return rows
    template_id = _position_template_by_code(db, current.code)
    if template_id is not None:
        return expectations_for_scope(db, "position_definition", template_id)
    return []


def seed_position_expectations(db: Session) -> int:
    """把内置职位模板期望写成数据（幂等：uq(scope kind, scope id, competency)）。"""
    from app.models.position import PositionDefinition

    created = 0
    positions = list(
        db.scalars(
            select(PositionDefinition).where(
                PositionDefinition.code.in_(list(BUILTIN_POSITION_EXPECTATIONS))
            )
        )
    )
    for position in positions:
        for competency_code, role in BUILTIN_POSITION_EXPECTATIONS.get(position.code, []):
            definition = _definition_by_code_global(db, competency_code)
            if definition is None:
                continue
            exists = db.scalar(
                select(CompetencyExpectation.id).where(
                    CompetencyExpectation.scope_kind == "position_definition",
                    CompetencyExpectation.scope_id == position.id,
                    CompetencyExpectation.competency_definition_id == definition.id,
                )
            )
            if exists is None:
                db.add(
                    CompetencyExpectation(
                        scope_kind="position_definition",
                        scope_id=position.id,
                        competency_definition_id=definition.id,
                        role=role,
                        weight=1.0,
                    )
                )
                created += 1
    return created

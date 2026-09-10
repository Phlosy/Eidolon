"""Assessment read + 受限 run 触发 API（P6，docs/evidence-pipeline.md §31~§33）。

    GET  /assessment-profiles                       档案列表（摘要）
    GET  /assessment-profiles/{id}                  档案详情（criteria + 多能力映射）
    GET  /employees/{id}/assessments                该员工考核历史（run 摘要）
    GET  /assessments/{id}                          单次考核详情（outputs + results）
    POST /employees/{id}/assessments/run            手动触发考核（受限：只能选档案，
                                                    分数由确定性引擎计算 —— 没有"提交最终分数"入口）
    GET  /employees/{id}/competencies/{comp}/explanation   能力解释（为什么是这个分）

解释层承诺：每个数字都能回链到 Evidence / Criterion / AssessmentRun。
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.models.assessment import (
    AssessmentCriterion,
    AssessmentCriterionCompetency,
    AssessmentProfile,
    AssessmentResult,
)
from app.models.competency import (
    AssessmentRun,
    CompetencyDefinition,
    CompetencyDomain,
    CompetencyEvidence,
    EmployeeCompetency,
)
from app.models.knowledge import Skill
from app.models.organization import Employee
from app.repositories import persons as person_repo
from app.schemas.assessment import (
    AssessmentProfileDetailOut,
    AssessmentProfileOut,
    AssessmentRunDetailOut,
    AssessmentRunSummaryOut,
    CompetencyExplanationOut,
)
from app.services import assessment as assessment_service
from app.services.competency import trend_direction_of

router = APIRouter(tags=["assessment"])


class RunRequest(BaseModel):
    #: 显式选档案（可选；缺省按任职职位解析）
    profile_code: str | None = None
    window_from: datetime | None = None


def _employee_or_404(db: Session, employee_id: int, company_id: int | None) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="employee not found")
    if company_id is not None and employee.company_id != company_id:
        raise HTTPException(status_code=404, detail="employee not found")
    return employee


def _profile_out(profile: AssessmentProfile) -> dict:
    return {
        "id": profile.id,
        "code": profile.code,
        "version": profile.version,
        "name": profile.name,
        "description": profile.description,
        "applies_to_kind": profile.applies_to_kind,
        "position_definition_id": profile.position_definition_id,
        "min_evidence_count": profile.min_evidence_count,
        "half_life_days": profile.half_life_days,
        "algorithm_version": profile.algorithm_version,
        "built_in": profile.built_in,
    }


def _criteria_out(db: Session, profile_id: int) -> list[dict]:
    criteria = list(
        db.scalars(
            select(AssessmentCriterion)
            .where(AssessmentCriterion.profile_id == profile_id)
            .order_by(AssessmentCriterion.order_index, AssessmentCriterion.id)
        )
    )
    mapping_rows: dict[int, list[AssessmentCriterionCompetency]] = {}
    definition_ids: set[int] = set()
    for criterion in criteria:
        mappings = list(
            db.scalars(
                select(AssessmentCriterionCompetency).where(
                    AssessmentCriterionCompetency.criterion_id == criterion.id
                )
            )
        )
        mapping_rows[criterion.id] = mappings
        definition_ids.update(row.competency_definition_id for row in mappings)
    definitions = {
        definition.id: definition
        for definition in db.scalars(
            select(CompetencyDefinition).where(CompetencyDefinition.id.in_(definition_ids))
        )
    }
    domains = {
        domain.id: domain
        for domain in db.scalars(
            select(CompetencyDomain).where(
                CompetencyDomain.id.in_(
                    {definition.domain_id for definition in definitions.values()}
                )
            )
        )
    }
    out: list[dict] = []
    for criterion in criteria:
        competencies = []
        for mapping in mapping_rows.get(criterion.id, []):
            definition = definitions.get(mapping.competency_definition_id)
            domain = domains.get(definition.domain_id) if definition else None
            competencies.append(
                {
                    "competency_definition_id": mapping.competency_definition_id,
                    "code": definition.code if definition else "",
                    "name": definition.name if definition else "",
                    "domain_code": domain.code if domain else "",
                    "domain_name": domain.name if domain else "",
                    "contribution_weight": mapping.contribution_weight,
                    "evidence_type": mapping.evidence_type,
                }
            )
        out.append(
            {
                "id": criterion.id,
                "code": criterion.code,
                "name": criterion.name,
                "description": criterion.description,
                "weight": criterion.weight,
                "order_index": criterion.order_index,
                "evidence_kinds": list(criterion.evidence_kinds or []),
                "competencies": competencies,
            }
        )
    return out


def _run_summary(db: Session, run: AssessmentRun) -> dict:
    profile = db.get(AssessmentProfile, run.profile_id) if run.profile_id else None
    return {
        "id": run.id,
        "employee_id": run.employee_id,
        "profile_id": run.profile_id,
        "profile_code": profile.code if profile else None,
        "profile_version": run.profile_version,
        "assessment_type": run.assessment_type,
        "triggered_by": run.triggered_by,
        "status": run.status,
        "window_from": run.window_from,
        "window_to": run.window_to,
        "evidence_count": len(run.evidence_ids or []),
        "inputs_hash": run.inputs_hash,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


@router.get("/assessment-profiles", response_model=list[AssessmentProfileOut])
def list_profiles(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    profiles = list(
        db.scalars(
            select(AssessmentProfile).order_by(AssessmentProfile.code, AssessmentProfile.version)
        )
    )
    return [_profile_out(profile) for profile in profiles]


@router.get("/assessment-profiles/{profile_id}", response_model=AssessmentProfileDetailOut)
def get_profile(profile_id: int, db: Session = Depends(get_db)) -> dict:
    profile = db.get(AssessmentProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    out = _profile_out(profile)
    out["criteria"] = _criteria_out(db, profile.id)
    return out


@router.get("/employees/{employee_id}/assessments", response_model=list[AssessmentRunSummaryOut])
def employee_assessments(
    employee_id: int,
    limit: int = Query(default=50, ge=1, le=200),
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    _employee_or_404(db, employee_id, company_id)
    runs = list(
        db.scalars(
            select(AssessmentRun)
            .where(AssessmentRun.employee_id == employee_id)
            .order_by(AssessmentRun.id.desc())
            .limit(limit)
        )
    )
    return [_run_summary(db, run) for run in runs]


@router.get("/assessments/{run_id}", response_model=AssessmentRunDetailOut)
def get_assessment(run_id: int, db: Session = Depends(get_db)) -> dict:
    run = db.get(AssessmentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="assessment not found")
    out = _run_summary(db, run)
    out["outputs"] = run.outputs or {}
    result_rows = list(
        db.scalars(
            select(AssessmentResult)
            .where(AssessmentResult.run_id == run.id)
            .order_by(AssessmentResult.kind, AssessmentResult.criterion_id)
        )
    )
    criterion_ids = {row.criterion_id for row in result_rows if row.criterion_id}
    criteria = {
        criterion.id: criterion
        for criterion in db.scalars(
            select(AssessmentCriterion).where(AssessmentCriterion.id.in_(criterion_ids))
        )
    }
    comp_ids = {row.competency_definition_id for row in result_rows if row.competency_definition_id}
    definitions = {
        definition.id: definition
        for definition in db.scalars(
            select(CompetencyDefinition).where(CompetencyDefinition.id.in_(comp_ids))
        )
    }
    results = []
    for row in result_rows:
        criterion = criteria.get(row.criterion_id) if row.criterion_id else None
        definition = definitions.get(row.competency_definition_id)
        results.append(
            {
                "kind": row.kind,
                "criterion_id": row.criterion_id,
                "criterion_code": criterion.code if criterion else None,
                "competency_definition_id": row.competency_definition_id,
                "competency_code": definition.code if definition else None,
                "observed_score": row.observed_score,
                "confidence": row.confidence,
                "evidence_count": row.evidence_count,
                "contribution": row.contribution,
                "rationale": row.rationale,
            }
        )
    out["results"] = results
    return out


@router.post(
    "/employees/{employee_id}/assessments/run",
    response_model=AssessmentRunDetailOut,
)
def trigger_assessment(
    employee_id: int,
    payload: RunRequest,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """受限的考核触发：只能选档案（或按任职职位推导），分数由确定性引擎计算。

    没有"提交最终分数"入口 —— 想造假得先伪造 Evidence（而且会被 normalizer 的
    源校验/幂等顶住）。
    """
    _employee_or_404(db, employee_id, company_id)
    profile = None
    if payload.profile_code:
        profile = db.scalar(
            select(AssessmentProfile).where(
                AssessmentProfile.code == payload.profile_code,
                AssessmentProfile.version == 1,
            )
        )
        if profile is None:
            raise HTTPException(status_code=404, detail="profile not found")
    try:
        run = assessment_service.run_assessment(
            db,
            employee_id,
            profile=profile,
            window_from=payload.window_from,
            assessment_type="manual",
            trigger_note="manual_api",
            commit=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return get_assessment(run.id, db)


def _resolve_competency_definition(db: Session, competency: str | int) -> CompetencyDefinition:
    if isinstance(competency, str) and not competency.isdigit():
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
    definition = db.get(CompetencyDefinition, int(competency))
    if definition is None:
        raise HTTPException(status_code=404, detail="competency not found")
    return definition


@router.get(
    "/employees/{employee_id}/competencies/{competency}/explanation",
    response_model=CompetencyExplanationOut,
)
def competency_explanation(
    employee_id: int,
    competency: str,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """能力解释：当前值 + 最近考核历史 + 证据 + 来源分布 + criterion 贡献 + 相关技能。"""
    _employee_or_404(db, employee_id, company_id)
    definition = _resolve_competency_definition(db, competency)
    domain = db.get(CompetencyDomain, definition.domain_id)

    row = db.scalar(
        select(EmployeeCompetency).where(
            EmployeeCompetency.employee_id == employee_id,
            EmployeeCompetency.competency_definition_id == definition.id,
        )
    )
    score = row.score if row else None
    confidence = row.confidence if row else None
    evidence_count = row.evidence_count if row else 0
    status = row.status if row else "unrated"
    trend = row.trend if row else None

    evidence_rows = list(
        db.scalars(
            select(CompetencyEvidence)
            .where(
                CompetencyEvidence.employee_id == employee_id,
                CompetencyEvidence.competency_definition_id == definition.id,
            )
            .order_by(CompetencyEvidence.occurred_at.desc())
            .limit(10)
        )
    )
    distribution_rows = db.execute(
        select(CompetencyEvidence.source_kind, func.count())
        .where(
            CompetencyEvidence.employee_id == employee_id,
            CompetencyEvidence.competency_definition_id == definition.id,
        )
        .group_by(CompetencyEvidence.source_kind)
    ).all()
    source_distribution = [
        {"source_kind": kind, "count": int(count)} for kind, count in distribution_rows
    ]

    runs = list(
        db.scalars(
            select(AssessmentRun)
            .where(AssessmentRun.employee_id == employee_id)
            .order_by(AssessmentRun.id.desc())
            .limit(5)
        )
    )
    profile_ids = {run.profile_id for run in runs if run.profile_id}
    profiles = {
        profile.id: profile
        for profile in db.scalars(
            select(AssessmentProfile).where(AssessmentProfile.id.in_(profile_ids))
        )
    }
    assessment_history = []
    run_ids = []
    for run in runs:
        run_ids.append(run.id)
        output = (run.outputs or {}).get(str(definition.id)) or {}
        profile = profiles.get(run.profile_id) if run.profile_id else None
        assessment_history.append(
            {
                "run_id": run.id,
                "profile_code": profile.code if profile else None,
                "profile_version": run.profile_version,
                "assessment_type": run.assessment_type,
                "triggered_by": run.triggered_by,
                "window_from": run.window_from,
                "window_to": run.window_to,
                "score": output.get("score"),
                "previous_score": output.get("previous_score"),
                "trend": output.get("trend"),
                "status": output.get("status"),
                "evidence_count": output.get("evidence_count"),
                "created_at": run.created_at,
            }
        )

    result_rows = list(
        db.scalars(
            select(AssessmentResult)
            .where(
                AssessmentResult.run_id.in_(run_ids or [0]),
                AssessmentResult.kind == "contribution",
                AssessmentResult.competency_definition_id == definition.id,
            )
            .order_by(AssessmentResult.id.desc())
            .limit(10)
        )
    )
    criterion_ids = {row.criterion_id for row in result_rows if row.criterion_id}
    criteria = {
        criterion.id: criterion
        for criterion in db.scalars(
            select(AssessmentCriterion).where(AssessmentCriterion.id.in_(criterion_ids))
        )
    }
    recent_criterion_results = [
        {
            "criterion_code": criteria[row.criterion_id].code
            if row.criterion_id in criteria
            else None,
            "criterion_name": criteria[row.criterion_id].name
            if row.criterion_id in criteria
            else None,
            "observed": row.observed_score,
            "confidence": row.confidence,
            "contribution": row.contribution,
            "evidence_count": row.evidence_count,
        }
        for row in result_rows
    ]

    skills = list(
        db.scalars(
            select(Skill).where(
                # R1.1：读口径切 person_id（repo 单一入口解析，带旧口径回落）
                person_repo.read_criterion(db, employee_id, Skill.person_id, Skill.employee_id),
                Skill.competency_definition_id == definition.id,
            )
        )
    )
    relevant_skills = [
        {
            "id": skill.id,
            "name": skill.name,
            "attempts": skill.attempts,
            "success_count": skill.success_count,
            "validation_status": skill.validation_status,
        }
        for skill in skills
    ]

    return {
        "competency_definition_id": definition.id,
        "code": definition.code,
        "name": definition.name,
        "domain_code": domain.code if domain else "",
        "domain_name": domain.name if domain else "",
        "score": score,
        "confidence": confidence,
        "evidence_count": evidence_count,
        "status": status,
        "trend": trend,
        "trend_direction": trend_direction_of(trend),
        "last_assessed_at": row.last_assessed_at if row else None,
        "assessment_history": assessment_history,
        "recent_evidence": [
            {
                "id": evidence.id,
                "source_kind": evidence.source_kind,
                "source_id": evidence.source_id,
                "source_ref": evidence.source_ref,
                "signal": evidence.signal,
                "strength": evidence.strength,
                "reliability": evidence.reliability,
                "occurred_at": evidence.occurred_at,
            }
            for evidence in evidence_rows
        ],
        "source_distribution": source_distribution,
        "recent_criterion_results": recent_criterion_results,
        "relevant_skills": relevant_skills,
    }

"""PositionFitEngine v1 —— 一对一匹配的确定性计算（P8，docs/position-fit.md）。

只读：不修改 EmployeeCompetency / Position / Assignment / Brain / Assessment。

算法（全部集中、可版本化）：
- requirement 级判定：evaluator.evaluate（UNRATED → INSUFFICIENT_CONFIDENCE →
  BELOW_MINIMUM → MEETS_MINIMUM → MEETS_TARGET）；
- known = 有 score 且 confidence ≥ minimum_confidence；unknown 不进惩罚（Unknown≠Bad）；
- coverage 永远基于**全部**需求（unknown 不会让 coverage 变 100%）；
- known fit = 对 known 需求按权重归一后加权 normalized_fit；
- fit_confidence = 加权 known competency confidence × required_coverage；
- Qualification / FitStatus 按 policy 阈值派生。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.competency import (
    CompetencyDefinition,
    CompetencyDomain,
    EmployeeCompetency,
    PositionCompetencyRequirement,
)
from app.models.position import PositionDefinition
from app.models.position_profile import PositionProfileVersion
from app.services import position_profile as profile_service
from app.talent.fit import hashing
from app.talent.fit.evaluator import classify, evaluate, normalized_fit
from app.talent.fit.models import (
    EvaluationStatus,
    FitStatus,
    GapType,
    PositionFitResult,
    QualificationStatus,
    RequirementEvaluation,
)
from app.talent.fit.policy import (
    POLICY,
    POSITION_FIT_ENGINE_VERSION,
    POSITION_FIT_POLICY_VERSION,
)

_CRITICAL_UNCERTAINTY = {EvaluationStatus.UNRATED, EvaluationStatus.INSUFFICIENT_CONFIDENCE}
_UNKNOWN_GAPS = {
    GapType.CRITICAL_UNCERTAINTY,
    GapType.REQUIRED_UNCERTAINTY,
}


def _competency_map(db: Session, definition_ids: set[int]) -> dict[int, CompetencyDefinition]:
    if not definition_ids:
        return {}
    return {
        definition.id: definition
        for definition in db.scalars(
            select(CompetencyDefinition).where(CompetencyDefinition.id.in_(definition_ids))
        )
    }


def _domain_map(db: Session, domain_ids: set[int]) -> dict[int, CompetencyDomain]:
    if not domain_ids:
        return {}
    return {
        domain.id: domain
        for domain in db.scalars(
            select(CompetencyDomain).where(CompetencyDomain.id.in_(domain_ids))
        )
    }


def _requirements(
    db: Session, version: PositionProfileVersion
) -> list[PositionCompetencyRequirement]:
    return profile_service.requirements_for(db, version)


def calculate(
    db: Session,
    *,
    employee_id: int,
    position: PositionDefinition,
    profile_version_id: int | None = None,
) -> PositionFitResult:
    """计算一名员工对一个职位的匹配（profile_version 缺省用 ACTIVE；无画像 → NOT_EVALUABLE）。"""
    position_id = int(position.id)
    now = datetime.now(UTC)

    version = None
    if profile_version_id is not None:
        version = db.get(PositionProfileVersion, profile_version_id)
        if version is None or version.position_definition_id != position_id:
            return _nonevaluable(position, reason="profile_version_not_found")
    else:
        version = profile_service.active_profile(db, position_id)

    result = PositionFitResult(
        employee_id=employee_id,
        position_definition_id=position_id,
        position_code=position.code,
        engine_version=POSITION_FIT_ENGINE_VERSION,
        policy_version=POSITION_FIT_POLICY_VERSION,
        calculated_at=now,
    )
    if version is None:
        result.fit_status = FitStatus.NOT_EVALUABLE
        result.qualification_status = QualificationStatus.INSUFFICIENT_DATA
        result.configured = False
        result.inputs_hash = hashing.inputs_hash(
            employee_id=employee_id,
            position_definition_id=position_id,
            profile_version=None,
            requirements=[],
            competencies=[],
        )
        return result

    requirements = _requirements(db, version)
    definitions = _competency_map(db, {req.competency_definition_id for req in requirements})
    domains = _domain_map(db, {definition.domain_id for definition in definitions.values()})
    competency_rows = {
        row.competency_definition_id: row
        for row in db.scalars(
            select(EmployeeCompetency).where(
                EmployeeCompetency.employee_id == employee_id,
                EmployeeCompetency.competency_definition_id.in_(
                    [req.competency_definition_id for req in requirements]
                ),
            )
        )
    }
    if position.assessment_profile_id:
        from app.models.assessment import AssessmentProfile

        assessment = db.get(AssessmentProfile, position.assessment_profile_id)
        if assessment is not None:
            result.assessment_profile_code = assessment.code

    evaluations: list[RequirementEvaluation] = []
    for req in requirements:
        definition = definitions.get(req.competency_definition_id)
        domain = domains.get(definition.domain_id) if definition else None
        evaluation = RequirementEvaluation(
            requirement_id=req.id,
            competency_definition_id=req.competency_definition_id,
            code=definition.code if definition else "",
            name=definition.name if definition else "",
            domain_code=domain.code if domain else "",
            domain_name=domain.name if domain else "",
            kind=domain.kind if domain else "general",
            requirement_type=req.requirement_type,
            critical=req.critical,
            minimum_score=req.minimum_score,
            target_score=req.target_score,
            minimum_confidence=req.minimum_confidence,
            weight=req.weight,
        )
        row = competency_rows.get(req.competency_definition_id)
        evaluate(
            evaluation,
            employee_score=row.score if row else None,
            employee_confidence=row.confidence if row else None,
        )
        evaluation.normalized_fit = normalized_fit(evaluation)
        classify(evaluation)
        evaluations.append(evaluation)

    result.configured = True
    result.profile_version_id = version.id
    result.profile_version = version.version
    result.profile_status = version.status
    result.requirement_evaluations = evaluations
    result.total_count = len(evaluations)
    result.known_count = sum(1 for item in evaluations if not item.is_unknown)
    _fill_main_numbers(result, evaluations)
    _fill_classifications(result, evaluations)
    _fill_status(result, evaluations)
    result.inputs_hash = hashing.inputs_hash(
        employee_id=employee_id,
        position_definition_id=position_id,
        profile_version=version.version,
        requirements=[
            {
                "requirement_id": req.id,
                "competency_definition_id": req.competency_definition_id,
                "requirement_type": req.requirement_type,
                "minimum_score": req.minimum_score,
                "target_score": req.target_score,
                "minimum_confidence": req.minimum_confidence,
                "critical": req.critical,
                "weight": req.weight,
            }
            for req in requirements
        ],
        competencies=[
            {
                "competency_definition_id": row.competency_definition_id,
                "score": row.score,
                "confidence": row.confidence,
                "last_assessed_at": row.last_assessed_at,
            }
            for row in competency_rows.values()
        ],
    )
    return result


def _nonevaluable(position: PositionDefinition, *, reason: str) -> PositionFitResult:
    return PositionFitResult(
        employee_id=0,
        position_definition_id=int(position.id),
        position_code=position.code,
        fit_status=FitStatus.NOT_EVALUABLE,
        qualification_status=QualificationStatus.INSUFFICIENT_DATA,
        configured=False,
        engine_version=POSITION_FIT_ENGINE_VERSION,
        policy_version=POSITION_FIT_POLICY_VERSION,
        calculated_at=datetime.now(UTC),
    )


def _fill_main_numbers(result: PositionFitResult, evaluations: list[RequirementEvaluation]) -> None:
    total = len(evaluations)
    required_total = sum(1 for item in evaluations if item.requirement_type == "required")
    preferred_total = sum(1 for item in evaluations if item.requirement_type == "preferred")
    known_required = sum(
        1 for item in evaluations if item.requirement_type == "required" and not item.is_unknown
    )
    known_preferred = sum(
        1 for item in evaluations if item.requirement_type == "preferred" and not item.is_unknown
    )
    result.requirement_coverage = round(result.known_count / total, 4) if total else 1.0
    result.required_coverage = round(known_required / required_total, 4) if required_total else 1.0
    result.preferred_coverage = (
        round(known_preferred / preferred_total, 4) if preferred_total else 1.0
    )

    known = [item for item in evaluations if not item.is_unknown]
    known_fit = _weighted_fit(known)
    result.known_fit_score = known_fit
    result.overall_fit_score = known_fit  # 内部值：UI 主状态由 coverage/status 决定

    general = [item for item in known if item.kind == "general"]
    professional = [item for item in known if item.kind == "professional"]
    if general:
        result.general_fit = _weighted_fit(general)
    if professional:
        result.professional_fit = _weighted_fit(professional)

    result.fit_confidence = _fit_confidence(known, result.required_coverage)


def _weighted_fit(known: list[RequirementEvaluation]) -> float | None:
    if not known:
        return None
    total_weight = sum(item.weight for item in known)
    if total_weight <= 0:
        total_weight = float(len(known))
    value = sum((item.weight / total_weight) * (item.normalized_fit or 0.0) for item in known)
    return round(value, 4)


def _fit_confidence(known: list[RequirementEvaluation], required_coverage: float) -> float | None:
    """fit_confidence = 加权 known competency confidence × required_coverage（0..1，确定性）。"""
    if not known:
        return None
    total_weight = sum(item.weight for item in known)
    if total_weight <= 0:
        total_weight = float(len(known))
    weighted_confidence = sum(
        (item.weight / total_weight) * (item.employee_confidence or 0.0) for item in known
    )
    confidence = weighted_confidence * required_coverage
    return round(max(0.0, min(1.0, confidence)), 4)


def _fill_classifications(
    result: PositionFitResult, evaluations: list[RequirementEvaluation]
) -> None:
    result.strengths = [item for item in evaluations if item.is_strength]
    result.gaps = [
        item
        for item in evaluations
        if item.gap_type
        in {
            GapType.CRITICAL_GAP,
            GapType.REQUIRED_GAP,
            GapType.TARGET_GAP,
            GapType.PREFERRED_GAP,
        }
    ]
    result.uncertainties = [
        item
        for item in evaluations
        if item.gap_type in _UNKNOWN_GAPS
        or (
            item.requirement_type == "preferred"
            and item.evaluation_status == EvaluationStatus.INSUFFICIENT_CONFIDENCE
        )
    ]
    result.development_opportunities = [
        item for item in evaluations if item.is_development_opportunity
    ]


def _fill_status(result: PositionFitResult, evaluations: list[RequirementEvaluation]) -> None:
    required_total = sum(1 for item in evaluations if item.requirement_type == "required")
    has_critical_gap = any(item.gap_type == GapType.CRITICAL_GAP for item in evaluations)
    has_critical_unknown = any(item.critical and item.is_unknown for item in evaluations)
    has_required_gap = any(
        item.gap_type in {GapType.REQUIRED_GAP, GapType.TARGET_GAP} for item in evaluations
    )
    insufficient_coverage = (
        required_total > 0 and result.required_coverage < POLICY.minimum_required_coverage
    )

    if has_critical_gap:
        result.qualification_status = QualificationStatus.NOT_QUALIFIED
    elif insufficient_coverage or has_critical_unknown:
        result.qualification_status = QualificationStatus.INSUFFICIENT_DATA
    elif has_required_gap:
        result.qualification_status = QualificationStatus.QUALIFIED_WITH_GAPS
    else:
        result.qualification_status = QualificationStatus.QUALIFIED

    if has_critical_gap:
        # 关键短板先于覆盖度报告：即使总体接近，也要让"存在关键缺陷"可见
        result.fit_status = FitStatus.CRITICAL_GAP
    elif insufficient_coverage or has_critical_unknown:
        result.fit_status = FitStatus.INSUFFICIENT_DATA
    elif result.known_fit_score is None:
        result.fit_status = FitStatus.INSUFFICIENT_DATA
    elif result.known_fit_score >= POLICY.strong_match:
        result.fit_status = FitStatus.STRONG_MATCH
    elif result.known_fit_score >= POLICY.partial_match:
        result.fit_status = FitStatus.PARTIAL_MATCH
    else:
        result.fit_status = FitStatus.WEAK_MATCH


def calculate_many(
    db: Session,
    *,
    position: PositionDefinition,
    employee_ids: list[int],
    profile_version_id: int | None = None,
) -> dict[int, PositionFitResult]:
    """一个职位 × 一批员工的批量匹配：共享 profile/requirements/definitions/domains，
    员工能力一次批量取 —— 每个候选不再重复读同一 Position Profile（N+1 守卫）。"""
    position_id = int(position.id)
    version = None
    if profile_version_id is not None:
        version = db.get(PositionProfileVersion, profile_version_id)
        if version is None or version.position_definition_id != position_id:
            return {
                employee_id: _nonevaluable(position, reason="profile_version_not_found")
                for employee_id in employee_ids
            }
    else:
        version = profile_service.active_profile(db, position_id)

    nonevaluable = _nonevaluable(position, reason="no_active_profile")
    if version is None:
        return {employee_id: nonevaluable for employee_id in employee_ids}

    requirements = _requirements(db, version)
    definitions = _competency_map(db, {req.competency_definition_id for req in requirements})
    domains = _domain_map(db, {definition.domain_id for definition in definitions.values()})
    competency_rows: dict[int, dict[int, EmployeeCompetency]] = {
        employee_id: {} for employee_id in employee_ids
    }
    for row in db.scalars(
        select(EmployeeCompetency).where(
            EmployeeCompetency.employee_id.in_(list(employee_ids)),
            EmployeeCompetency.competency_definition_id.in_(
                [req.competency_definition_id for req in requirements]
            ),
        )
    ):
        competency_rows.setdefault(row.employee_id, {})[row.competency_definition_id] = row
    now = datetime.now(UTC)
    assessment_code = None
    if position.assessment_profile_id:
        from app.models.assessment import AssessmentProfile

        _assessment = db.get(AssessmentProfile, position.assessment_profile_id)
        assessment_code = _assessment.code if _assessment else None
    results: dict[int, PositionFitResult] = {}
    for employee_id in employee_ids:
        result = PositionFitResult(
            employee_id=employee_id,
            position_definition_id=position_id,
            position_code=position.code,
            engine_version=POSITION_FIT_ENGINE_VERSION,
            policy_version=POSITION_FIT_POLICY_VERSION,
            calculated_at=now,
        )
        result.configured = True
        result.profile_version_id = version.id
        result.profile_version = version.version
        result.profile_status = version.status
        result.assessment_profile_code = assessment_code

        evaluations: list[RequirementEvaluation] = []
        rows = competency_rows.get(employee_id, {})
        for req in requirements:
            definition = definitions.get(req.competency_definition_id)
            domain = domains.get(definition.domain_id) if definition else None
            evaluation = RequirementEvaluation(
                requirement_id=req.id,
                competency_definition_id=req.competency_definition_id,
                code=definition.code if definition else "",
                name=definition.name if definition else "",
                domain_code=domain.code if domain else "",
                domain_name=domain.name if domain else "",
                kind=domain.kind if domain else "general",
                requirement_type=req.requirement_type,
                critical=req.critical,
                minimum_score=req.minimum_score,
                target_score=req.target_score,
                minimum_confidence=req.minimum_confidence,
                weight=req.weight,
            )
            row = rows.get(req.competency_definition_id)
            evaluate(
                evaluation,
                employee_score=row.score if row else None,
                employee_confidence=row.confidence if row else None,
            )
            evaluation.normalized_fit = normalized_fit(evaluation)
            classify(evaluation)
            evaluations.append(evaluation)

        result.requirement_evaluations = evaluations
        result.total_count = len(evaluations)
        result.known_count = sum(1 for item in evaluations if not item.is_unknown)
        _fill_main_numbers(result, evaluations)
        _fill_classifications(result, evaluations)
        _fill_status(result, evaluations)
        result.inputs_hash = hashing.inputs_hash(
            employee_id=employee_id,
            position_definition_id=position_id,
            profile_version=version.version,
            requirements=[
                {
                    "requirement_id": req.id,
                    "competency_definition_id": req.competency_definition_id,
                    "requirement_type": req.requirement_type,
                    "minimum_score": req.minimum_score,
                    "target_score": req.target_score,
                    "minimum_confidence": req.minimum_confidence,
                    "critical": req.critical,
                    "weight": req.weight,
                }
                for req in requirements
            ],
            competencies=[
                {
                    "competency_definition_id": entry.competency_definition_id,
                    "score": entry.score,
                    "confidence": entry.confidence,
                    "last_assessed_at": entry.last_assessed_at,
                }
                for entry in rows.values()
            ],
        )
        results[employee_id] = result
    return results

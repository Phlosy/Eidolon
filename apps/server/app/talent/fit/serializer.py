"""Fit 序列化出口（P8）—— 派生字段显式计算，不落 schema 默认值（ADR-12）。

unrated ≠ 0；no profile ≠ fit 0；insufficient coverage ≠ weak match；未算 ≠ false。
reason_code 是机器码，文案交给前端 i18n。
"""

from __future__ import annotations

from app.talent.fit.models import PositionFitResult

SERIALIZER_VERSION = "v1"


def _evaluation_payload(evaluation) -> dict:
    return {
        "requirement_id": evaluation.requirement_id,
        "competency_definition_id": evaluation.competency_definition_id,
        "code": evaluation.code,
        "name": evaluation.name,
        "domain_code": evaluation.domain_code,
        "domain_name": evaluation.domain_name,
        "kind": evaluation.kind,
        "requirement_type": evaluation.requirement_type,
        "critical": evaluation.critical,
        "minimum_score": evaluation.minimum_score,
        "target_score": evaluation.target_score,
        "minimum_confidence": evaluation.minimum_confidence,
        "weight": evaluation.weight,
        # 员工侧（null = 未评估，不是 0）
        "employee_score": evaluation.employee_score,
        "employee_confidence": evaluation.employee_confidence,
        "evaluation_status": evaluation.evaluation_status,
        "reason_code": evaluation.reason_code,
        "gap_type": evaluation.gap_type,
        "is_strength": evaluation.is_strength,
        "is_development_opportunity": evaluation.is_development_opportunity,
        "is_unknown": evaluation.is_unknown,
        "normalized_fit": evaluation.normalized_fit,
        "margin_to_minimum": evaluation.margin_to_minimum,
        "margin_to_target": evaluation.margin_to_target,
    }


def serialize(result: PositionFitResult) -> dict:
    return {
        "employee_id": result.employee_id,
        "position_definition_id": result.position_definition_id,
        "position_code": result.position_code,
        "configured": result.configured,
        "profile_version_id": result.profile_version_id,
        "profile_version": result.profile_version,
        "profile_status": result.profile_status,
        "assessment_profile_code": result.assessment_profile_code,
        "fit_status": result.fit_status,
        "qualification_status": result.qualification_status,
        # 内部值 + 展示元数据：单看数字会有虚假精确感，UI 必须并列 coverage/confidence
        "known_fit_score": result.known_fit_score,
        "overall_fit_score": result.overall_fit_score,
        "fit_confidence": result.fit_confidence,
        "requirement_coverage": result.requirement_coverage,
        "required_coverage": result.required_coverage,
        "preferred_coverage": result.preferred_coverage,
        "known_count": result.known_count,
        "total_count": result.total_count,
        "general_fit": result.general_fit,
        "professional_fit": result.professional_fit,
        "strengths": [_evaluation_payload(item) for item in result.strengths],
        "gaps": [_evaluation_payload(item) for item in result.gaps],
        "uncertainties": [_evaluation_payload(item) for item in result.uncertainties],
        "development_opportunities": [
            _evaluation_payload(item) for item in result.development_opportunities
        ],
        "requirement_evaluations": [
            _evaluation_payload(item) for item in result.requirement_evaluations
        ],
        "engine_version": result.engine_version,
        "policy_version": result.policy_version,
        "serializer_version": SERIALIZER_VERSION,
        "inputs_hash": result.inputs_hash,
        "calculated_at": result.calculated_at,
    }

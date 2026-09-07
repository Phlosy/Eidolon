"""Position Fit read schemas（P8）。派生字段全部由 serializer 计算（ADR-12）。
reason_code 是机器码，文案由前端 i18n；unrated≠0、no profile≠fit 0。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FitEvaluationOut(BaseModel):
    requirement_id: int
    competency_definition_id: int
    code: str
    name: str
    domain_code: str
    domain_name: str
    kind: str
    requirement_type: str
    critical: bool
    minimum_score: int | None = None
    target_score: int | None = None
    minimum_confidence: float | None = None
    weight: float
    employee_score: int | None = None
    employee_confidence: float | None = None
    evaluation_status: str
    reason_code: str
    gap_type: str | None = None
    is_strength: bool
    is_development_opportunity: bool
    is_unknown: bool
    normalized_fit: float | None = None
    margin_to_minimum: int | None = None
    margin_to_target: int | None = None


class PositionFitOut(BaseModel):
    employee_id: int
    position_definition_id: int
    position_code: str
    configured: bool
    profile_version_id: int | None = None
    profile_version: int | None = None
    profile_status: str | None = None
    assessment_profile_code: str | None = None
    fit_status: str
    qualification_status: str
    known_fit_score: float | None = None
    overall_fit_score: float | None = None
    fit_confidence: float | None = None
    requirement_coverage: float
    required_coverage: float
    preferred_coverage: float
    known_count: int
    total_count: int
    general_fit: float | None = None
    professional_fit: float | None = None
    strengths: list[FitEvaluationOut]
    gaps: list[FitEvaluationOut]
    uncertainties: list[FitEvaluationOut]
    development_opportunities: list[FitEvaluationOut]
    requirement_evaluations: list[FitEvaluationOut]
    engine_version: str
    policy_version: str
    serializer_version: str
    inputs_hash: str
    calculated_at: datetime | None = None

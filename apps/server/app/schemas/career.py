"""Career read/write schemas（P10）。派生字段全部由序列化出口计算（ADR-12）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class CareerExperienceOut(BaseModel):
    tasks_completed: int
    projects_completed: int
    reviews_presented: int
    assessments_count: int
    active_plans: int


class CareerReadinessOut(BaseModel):
    employee_id: int
    target_position: dict
    position_fit: dict
    critical_gaps: list[str]
    uncertainties: list[str]
    required_gaps: list[str]
    experience: CareerExperienceOut
    experience_readiness: dict
    readiness_status: (
        str  # READY / NEAR_READY / DEVELOPMENT_NEEDED / NEEDS_EVIDENCE / CRITICAL_GAPS
    )
    reasons: list[str]
    career_analysis_version: str
    inputs_hash: str


class DevelopmentNeedOut(BaseModel):
    competency_definition_id: int
    code: str
    name: str
    need_type: str
    current_score: int | None = None
    current_confidence: float | None = None
    minimum_required: int | None = None
    target_required: int | None = None
    gap_to_minimum: int | None = None
    gap_to_target: int | None = None
    critical: bool
    source: str


class DevelopmentPlanItemOut(BaseModel):
    id: int
    plan_id: int
    competency_definition_id: int
    code: str
    name: str
    need_type: str
    objective: str
    target_score: int | None = None
    target_confidence: float | None = None
    priority: int
    status: str
    recommended_actions: list[str]
    progress_metadata: dict


class DevelopmentPlanOut(BaseModel):
    id: int
    employee_id: int
    target_position_definition_id: int | None = None
    target_position: dict | None = None
    status: str
    title: str
    description: str
    source_fit_hash: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    items: list[DevelopmentPlanItemOut]


class CareerEventOut(BaseModel):
    type: str
    at: datetime | None = None
    title: str
    reason: str
    source: str


class CareerOverviewOut(BaseModel):
    employee_id: int
    current_position: dict | None = None
    next_positions: list[dict]
    plans: list[dict]
    timeline: list[CareerEventOut]


class CareerNextStepOut(BaseModel):
    target_position: dict
    transition_type: str
    readiness_status: str
    position_fit: dict
    required_gaps: list[str]
    uncertainties: list[str]


# ------------------------------------------------------------- 写请求


class CreatePlanIn(BaseModel):
    target_position_definition_id: int | None = None
    title: str | None = None


class PatchPlanIn(BaseModel):
    title: str | None = None
    description: str | None = None


class PositionChangeIn(BaseModel):
    target_definition_id: int
    slot_id: int
    reason: str


class PositionChangeOut(BaseModel):
    assignment_id: int
    event_type: str
    warnings: list[str]
    readiness: CareerReadinessOut
